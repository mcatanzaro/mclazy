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

# Copyright (C) 2014
#    Richard Hughes <richard@hughsie.com>

""" Parses the modules.xml file """

from xml.etree.ElementTree import ElementTree

class ModulesItem(object):
    """ Represents a project in the modules.xml file """
    def __init__(self):
        self.name = None
        self.pkgname = None
        self.release = None
        self.disabled = False
        self.release_glob = {}

        # add the default gnome release numbers
        self.release_glob['f38'] = "40.*,40,41.*,41,42.*,42,43.*,43,3.47.*,3.48.*,44.*,44"
        self.release_glob['f39'] = self.release_glob['f38'] + ",3.49.*,3.50.*,45.*,45"
        self.release_glob['f40'] = self.release_glob['f39'] + ",3.51.*,3.52.*,46.*,46"
        self.release_glob['f41'] = self.release_glob['f40'] + ",3.53.*,3.54.*,47.*,47"
        self.release_glob['rawhide'] = "*"

class ModulesXml(object):
    """ Parses the modules.xml file """

    def __init__(self, filename):
        self.items = []
        tree = ElementTree()
        tree.parse(filename)
        projects = list(tree.iter("project"))
        for project in projects:
            item = ModulesItem()
            item.disabled = False
            item.name = project.get('name')
            item.pkgname = project.get('pkgname')
            if not item.pkgname:
                item.pkgname = item.name
            if project.get('disabled') == "True":
                item.disabled = True
            for data in project:
                if data.tag == 'release':
                    version = data.get('version')
                    item.release_glob[version] = data.text
            item.releases = []
            if project.get('releases'):
                for release in project.get('releases').split(','):
                    item.releases.append(release)
            else:
                item.releases.append('f34')
                item.releases.append('f35')
                item.releases.append('f36')
                item.releases.append('f37')
            self.items.append(item)

    def _print(self):
        for item in self.items:
            print(item.pkgname)

    def _get_item_by_name(self, name):
        for item in self.items:
            if item.name == name:
                return item
        return None
