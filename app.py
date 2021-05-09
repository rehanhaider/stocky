#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Stocky McStockface for Indian stock markets
# https://github.com/justgoodin/stocky
# Copyright (C) 2021  Rehan Haider (justgoodin)
#
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.


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
