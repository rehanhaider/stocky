from termcolor import colored


class ScreenManager:
    def __init__(self) -> None:
        print(colored("--------------------------------------------", "green"))
        print(colored("Choose an option (1-9)", "green"))
        print(colored("--------------------------------------------", "green"))
        print(colored("1. Rebuild stocky.db from scratch", "green"))
        print(colored("2. Update all Yahoo data", "green"))
        print(colored("3. Exit", "green"))
        print(colored("--------------------------------------------", "green"))

    def getUserInput() -> int:
        try:
            choice = int(input("Your choice: ").strip())
        except ValueError:
            choice = -1
        print(colored("--------------------------------------------", "green"))
        if not 0 < choice < 4:
            choice = -1

        return choice

    def getConfirmation() -> bool:
        print(colored("--------------------------------------------", "green"))
        choice = input("Enter (Y)es to confirm, any key to cancel: ").strip()
        return choice.lower() in ["y", "yes"]

    def yahoo():
        from lib.bin import yahooDataManager

        ydm = yahooDataManager()
