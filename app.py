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

from lib.bse import get_bhav_bse
from lib.art import print_welcome_message


def main():
    """
    Main function
    """
    print_welcome_message()

    # get bhavcopy from BSE

    print("Getting bhavcopy from BSE")


# print options
if __name__ == "__main__":
    main()
