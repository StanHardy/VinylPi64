from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


import base64
import json
from typing import Optional

import requests
from PIL import Image

from vinylpi.config.runtime import read_config, write_config
from vinylpi.integrations.pixoo_discovery import discover_pixoo_ip, _probe_ip  # Compatibility for existing integrations/tests

from vinylpi.paths import CLOUD_BASE_URL

class PixooError(Exception):
    pass


class PixooClient:
    def __init__(self, ip: Optional[str] = None, timeout: Optional[float] = None, *, auto_discover: bool = True):
        CONFIG = read_config()
        divoom_cfg = CONFIG.get("divoom", {})
        debug_log = CONFIG["debug"]["logs"]

        self.timeout = timeout if timeout is not None else divoom_cfg.get("timeout", 0.3)
        self.gif_speed_ms = divoom_cfg.get("gif_speed_ms", 100)
        self.auto_reset_gif_id = divoom_cfg.get("auto_reset_gif_id", True)

        cfg_ip = str(divoom_cfg.get("ip") or "").strip()
        self._auto_discover = bool(auto_discover)
        self.ip = str(ip or cfg_ip).strip()
        if not self.ip and self._auto_discover:
            # A new profile may have neither a saved device nor discovery enabled.
            discovery = divoom_cfg.get("discovery") or {}
            if not discovery.get("enabled", True):
                raise PixooError(
                    "Pixoo not configured. Select a device in Settings > Pixoo & Network."
                )
            # First-time setup: discover only when there is no saved IP.
            self.ip = self._discover_and_save(previous_ip="")
        self.base_url = f"http://{self.ip}/post" if self.ip else ""

    def _discover_and_save(self, *, previous_ip: str) -> str:
        discovered = discover_pixoo_ip()
        if not discovered:
            raise PixooError(
                "Pixoo not found on the local network. "
                "Check the device or select it in Settings > Pixoo & Network."
            )
        if discovered != previous_ip:
            try:
                write_config({"divoom": {"ip": discovered}})
                logger.info(f"Pixoo IP discovered and saved: {discovered}")
            except Exception as exc:
                logger.warning(f"could not save Pixoo IP to config: {exc}")
        return discovered


    def _cloud_post(self, path: str, payload: dict) -> dict:
        url = f"{CLOUD_BASE_URL}{path}"
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise PixooError(f"HTTP error on Divoom cloud API: {e}") from e

        try:
            data = resp.json()
        except json.JSONDecodeError:
            raise PixooError("Invalid JSON from Divoom cloud API")

        if isinstance(data, dict) and data.get("ReturnCode", 0) != 0:
            msg = data.get("ReturnMessage", "unknown error")
            raise PixooError(f"Divoom cloud API error: {msg}")

        return data

    def get_liked_gifs(self, page: int = 1) -> list[dict]:
        CONFIG = read_config()
        divoom_cfg = CONFIG.get("divoom", {})
        device_id = divoom_cfg.get("device_id")
        device_mac = divoom_cfg.get("device_mac")

        if not device_id or not device_mac:
            raise PixooError(
                "Missing divoom.device_id/device_mac in config.json "
                "(required for community GIFs)."
            )

        payload = {
            "DeviceId": device_id,
            "DeviceMac": device_mac,
            "Page": int(page),
        }

        data = self._cloud_post("/Device/GetImgLikeList", payload)
        img_list = data.get("ImgList") or []

        result = []
        for item in img_list:
            result.append({
                "file_name": item.get("FileName", ""),
                "file_id": item.get("FileId", ""),
            })
        return result


    def _post(self, payload: dict) -> dict:
        if not self.base_url:
            raise PixooError("Pixoo not configured. Select a device in Settings > Pixoo & Network.")
        try:
            resp = requests.post(self.base_url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except (requests.ConnectionError, requests.Timeout) as exc:
            if not self._auto_discover:
                raise PixooError(
                    f"Pixoo not reachable at {self.ip}. Check its network connection."
                ) from exc
            # A *real* network failure is the fallback trigger. Never scan
            # merely because a PixooClient was instantiated or polled.
            old_ip = self.ip
            try:
                new_ip = self._discover_and_save(previous_ip=old_ip)
            except PixooError as discovery_exc:
                raise PixooError(
                    f"Pixoo not reachable at {old_ip}; automatic discovery also failed. "
                    "Check Settings > Pixoo & Network."
                ) from discovery_exc
            self.ip = new_ip
            self.base_url = f"http://{new_ip}/post"
            try:
                resp = requests.post(self.base_url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
            except requests.RequestException as retry_exc:
                raise PixooError(f"Pixoo request failed after discovery: {retry_exc}") from retry_exc
        except requests.RequestException as exc:
            # HTTP errors do not indicate that the address changed.
            raise PixooError(f"HTTP error while contacting Pixoo: {exc}") from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise PixooError("Invalid JSON from Pixoo local API") from exc

        if isinstance(data, dict) and data.get("error_code", 0) != 0:
            raise PixooError(f"Pixoo API error: {data!r}")

        return data


    def get_next_pic_id(self) -> int:
        data = self._post({"Command": "Draw/GetHttpGifId"})
        pic_id = data.get("PicId") or data.get("PicID")
        if pic_id is None:
            pic_id = 1
        return int(pic_id)

    def reset_pic_id(self) -> None:
        self._post({"Command": "Draw/ResetHttpGifId"})

    @staticmethod
    def _image_to_rgb_bytes(img: Image.Image) -> bytes:
        if img.mode != "RGB":
            img = img.convert("RGB")

        width, height = img.size
        if width != height:
            raise PixooError(f"Pixoo expects quadratic image, received: {img.size}")

        if width not in (16, 32, 64):
            raise PixooError(
                f"Pixoo only supports 16, 32 or 64 pixel width/height, received: {width}"
            )

        # PIL's native encoder preserves the exact row-major RGB bytes while
        # avoiding 4,096 Python-level getpixel calls per 64x64 Pixoo frame.
        return img.tobytes()

    def send_frame(self, frame: Image.Image, *, speed_ms: Optional[int] = None) -> None:
        width, height = frame.size
        if width != height:
            raise PixooError(f"Pixoo expects quadratic image, received: {frame.size}")

        raw_rgb = self._image_to_rgb_bytes(frame)
        pic_data_b64 = base64.b64encode(raw_rgb).decode("ascii")

        if self.auto_reset_gif_id:
            self.reset_pic_id() 

        pic_id = self.get_next_pic_id() 

        speed = self.gif_speed_ms if speed_ms is None else int(speed_ms)

        payload = {
            "Command": "Draw/SendHttpGif",
            "PicNum": 1,
            "PicWidth": width,
            "PicOffset": 0,
            "PicID": pic_id,
            "PicSpeed": speed,
            "PicSpped": speed,
            "PicData": pic_data_b64,
        }

        self._post(payload)

    def discover_cloud_device(self) -> dict:
        url = f"{CLOUD_BASE_URL}/Device/ReturnSameLANDevice"
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise PixooError(f"Error calling Divoom discovery API: {e}") from e

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise PixooError("Invalid JSON from Divoom discovery API") from e

        devices = data.get("DeviceList") or []
        if not devices:
            raise PixooError("No Divoom devices found in ReturnSameLANDevice response")

        dev = devices[0] 

        return {
            "device_name": dev.get("DeviceName"),
            "device_id": dev.get("DeviceId") or dev.get("DeviceID"),
            "device_mac": dev.get("DeviceMac") or dev.get("MacAddress"),
            "device_private_ip": dev.get("DevicePrivateIP"),
        }


    def show_image_file(self, path: str, *, resize_to: int = 64) -> None:
        img = Image.open(path).convert("RGB")
        if img.size != (resize_to, resize_to):
            img = img.resize((resize_to, resize_to), Image.Resampling.BILINEAR)
        self.send_frame(img)

    def play_remote_gif(self, file_id: str) -> None:
        if not file_id:
            raise PixooError("file_id must not be empty")

        self._post({
            "Command": "Draw/SendRemote",
            "FileId": file_id,
        })


    def play_gif_url(self, url: str) -> None:
        """Ask the Pixoo to download and loop an HTTP-hosted GIF.

        A successful command response does not confirm that the device managed
        to fetch the URL. The calling display loop can only verify the local
        server's availability, not playback on the Pixoo itself.
        """
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        if parsed.scheme != "http" or not parsed.netloc or parsed.username or parsed.password:
            raise PixooError("Pixoo GIF URL must use HTTP")
        if len(url.encode("utf-8")) > 255:
            raise PixooError("Pixoo GIF URL is too long")
        self._post({"Command": "Device/PlayTFGif", "FileType": 2, "FileName": url})


    def get_all_conf(self) -> dict:
        return self._post({"Command": "Channel/GetAllConf"})

    def set_brightness(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        self._post({
            "Command": "Channel/SetBrightness",
            "Brightness": value,
        })

    def reboot(self) -> None:
        self._post({"Command": "Device/SysReboot"})

    def set_channel(self, index: int) -> None:
        """
        0:Faces;
        1:Cloud Channdle;
        2:Visualizer;
        3:Custom;
        4:black screen
        """
        self._post({
            "Command": "Channel/SetIndex",
            "SelectIndex": int(index),
        })
