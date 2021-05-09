import sys
from termcolor import colored
from lib.screen.screenManager import ScreenManager
from lib.bin.bhavCopy import bhavUpdate
from lib.bin.yahoo import yahooDataManager

_DEFAULT = 0
_BHAV = 1
_YAHOO = 2
_EXIT = 3
_ERROR = -1


def main():
    exitApp = False
    screen = _DEFAULT
    while True:
        if exitApp:
            sys.exit(0)

        if screen == _DEFAULT:
            ScreenManager()
            option = ScreenManager.getUserInput()

        elif option == _ERROR:
            print(colored("Not a valid selection. Please choose a valid option from below", "red"))
            continue
        if option == _BHAV:
            screen = 1
            print(colored("Rebuilding will delete the previous version.", "yellow", "on_red"))
            if ScreenManager.getConfirmation():
                bhavUpdate()
            screen = _DEFAULT
        elif option == _YAHOO:
            print(colored("This will download all data and may take a long time.", "yellow", "on_red"))
            if ScreenManager.getConfirmation():
                ydm = yahooDataManager()
                ydm.updateData()
            screen = _DEFAULT
        if option == _EXIT:
            print(colored("Terminate program and exit?", "yellow", "on_red"))
            if ScreenManager.getConfirmation():
                exitApp = True


# print options
if __name__ == "__main__":
    main()
