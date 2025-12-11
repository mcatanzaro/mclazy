#!/usr/bin/python3
# Licensed under the GNU General Public License Version 2
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Red Hat

""" Parses the branches.xml file """

from xml.etree.ElementTree import ElementTree
from enum import StrEnum, auto
from log import print_warning

class Branch(object):
    """ Represents a branch in the branches.xml file """
    def __init__(self):
        self.name = None
        self.release_tag = None
        self.gnome_version = None
        self.eol = False

class BranchesXml(dict):
    """ Parses the branches.xml file """

    def __init__(self, filename):
        tree = ElementTree()
        tree.parse(filename)

        for branch in tree.iter("branch"):
            item = Branch()
            item.name = branch.get('name')
            self[item.name] = item

            for data in branch:
                if data.tag == 'tag':
                    item.release_tag = data.text
                elif data.tag == 'gnome':
                    item.gnome_version = int(data.text)
                elif data.tag == 'alias':
                    if data.text in self:
                        print_warning("Duplicated <alias> in branches.xml, skipping")
                        continue
                    self[data.text] = item
                elif data.tag == 'eol':
                    item.eol = True

    def default_version_limits(self):
        version_limits = {}
        for branch in self.values():
            # We add one to the GNOME version because the version_limit is an
            # upper bound. If a branch is tracking GNOME 48, then we want to
            # set the version_limit to "49", which means "update to any release
            # whose version is less than 49" or in other words "update to the
            # last release of 48"
            version_limits[branch.name] = str(branch.gnome_version + 1)
        return version_limits
