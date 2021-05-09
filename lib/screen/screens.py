def yahooScreen(screen: int = 0) -> None:
    def default() -> None:
        pass

    def updateOptions():
        pass

    if screen == 0:
        screen = default
    elif screen == 1:
        screen = updateOptions

    return screen
