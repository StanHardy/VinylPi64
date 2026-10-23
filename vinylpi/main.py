from vinylpi.core.runner import main_loop

if __name__ == "__main__":
    from vinylpi.logging_service import install_print_capture
    install_print_capture("Vinyl")
    main_loop()
