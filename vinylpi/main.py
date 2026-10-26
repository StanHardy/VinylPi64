from vinylpi.core.runner import main_loop

if __name__ == "__main__":
    from vinylpi.logging_service import configure_logging
    configure_logging("Vinyl")
    main_loop()
