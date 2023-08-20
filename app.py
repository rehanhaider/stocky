"""
# -*- coding: utf-8 -*-
#
# Stocky McStockface for Indian stock markets
# https://github.com/justgoodin/stocky
# Copyright (C) 2021  Rehan Haider (justgoodin)
#
#
# This program is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

from rich import print as rprint

from lib.welcome import print_welcome_message, print_options
from lib.merge_data import merge_isin
from lib.refresh_data import refresh_data
from lib.structure import create_folders


def main():
    """
    Main function
    """
    print_welcome_message()
    create_folders()

    run_screen = True
    while run_screen:
        # print options
        print_options()

        # get user input
        user_input = input("Enter your choice: ")

        # process user input
        match user_input:
            case "1":
                merge_isin()
            case "2":
                refresh_data()
            case "3":
                rprint("Exiting...")
                run_screen = False


# print options
if __name__ == "__main__":
    main()
