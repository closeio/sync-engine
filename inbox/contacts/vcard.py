# Originally from pycarddav
#
# Copyright (c) 2011-2014 Christian Geier & contributors
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
The pycarddav abstract model and tools for VCard handling.
"""


import base64
import contextlib
import logging
import sys
from collections import defaultdict

import vobject

NTEXT = "\x1b[0m"
BTEXT = "\x1b[1m"


def fix_vobject(vcard):  # noqa: ANN201
    """
    Trying to fix some more or less common errors in vcards

    for now only missing FN properties are handled (and reconstructed from N)
    :type vcard: vobject.base.Component (vobject based vcard)

    """  # noqa: D401
    if "fn" not in vcard.contents:
        logging.debug("vcard has no formatted name, reconstructing...")
        fname = vcard.contents["n"][0].valueRepr()
        fname = fname.strip()
        vcard.add("fn")
        vcard.fn.value = fname
    return vcard


def vcard_from_vobject(vcard):  # noqa: ANN201
    vcard = fix_vobject(vcard)
    vdict = VCard()
    if vcard.name != "VCARD":
        raise Exception  # TODO proper Exception type
    for line in vcard.getChildren():
        # this might break, was tried/excepted before
        line.transformFromNative()
        property_name = line.name
        property_value = line.value

        with contextlib.suppress(AttributeError):
            if line.ENCODING_paramlist in (["b"], ["B"]):
                property_value = base64.b64encode(line.value)
        if isinstance(property_value, list):
            property_value = (",").join(property_value)

        vdict[property_name].append((property_value, line.params))
    return vdict


def vcard_from_string(vcard_string):  # noqa: ANN201
    """
    vcard_string: str
    returns VCard()
    """
    try:
        vcard = vobject.readOne(vcard_string)
    except vobject.base.ParseError as error:
        raise Exception(error)  # TODO proper exception  # noqa: B904
    return vcard_from_vobject(vcard)


class VCard(defaultdict):
    """
    internal representation of a VCard. This is dict with some
    associated methods,
    each dict item is a list of tuples
    i.e.:
    >>> VCard['EMAIL']
    [('hanz@wurst.com', ['WORK', 'PREF']), ('hanz@wurst.net', ['HOME'])]

    self.href: unique id (really just the url) of the VCard
    self.account: account which this card is associated with
    db_path: database file from which to initialize the VCard

    self.edited:
        0: nothing changed
        1: name and/or fname changed
        2: some property was deleted
    """

    def __init__(self, ddict: str = "") -> None:
        if ddict == "":
            defaultdict.__init__(self, list)
        else:
            defaultdict.__init__(self, list, ddict)
        self.href = ""
        self.account = ""
        self.etag = ""
        self.edited = 0

    def serialize(self):  # noqa: ANN201
        return repr(list(self.items()))

    @property
    def name(self):  # noqa: ANN201
        return str(self["N"][0][0]) if self["N"] else ""

    @name.setter
    def name(self, value) -> None:
        if not self["N"]:
            self["N"] = [("", {})]
        self["N"][0][0] = value

    @property
    def fname(self):  # noqa: ANN201
        return str(self["FN"][0][0]) if self["FN"] else ""

    @fname.setter
    def fname(self, value) -> None:
        self["FN"][0] = (value, {})

    def alt_keys(self):  # noqa: ANN201
        keylist = list(self)
        for one in [x for x in ["FN", "N", "VERSION"] if x in keylist]:
            keylist.remove(one)
        keylist.sort()
        return keylist

    def print_email(self):  # noqa: ANN201
        """Prints only name, email and type for use with mutt"""  # noqa: D401
        collector = list()
        try:
            for one in self["EMAIL"]:
                try:
                    typelist = ",".join(one[1]["TYPE"])
                except KeyError:
                    typelist = ""
                collector.append(one[0] + "\t" + self.fname + "\t" + typelist)
            return "\n".join(collector)
        except KeyError:
            return ""

    def print_tel(self):  # noqa: ANN201
        """Prints only name, email and type for use with mutt"""  # noqa: D401
        collector = list()
        try:
            for one in self["TEL"]:
                try:
                    typelist = ",".join(one[1]["TYPE"])
                except KeyError:
                    typelist = ""
                collector.append(self.fname + "\t" + one[0] + "\t" + typelist)
            return "\n".join(collector)
        except KeyError:
            return ""

    @property
    def pretty(self):  # noqa: ANN201
        return self._pretty_base(self.alt_keys())

    @property
    def pretty_min(self):  # noqa: ANN201
        return self._pretty_base(["TEL", "EMAIL"])

    def _pretty_base(self, keylist):
        collector = list()
        if sys.stdout.isatty():
            collector.append("\n" + BTEXT + "Name: " + self.fname + NTEXT)
        else:
            collector.append("\n" + "Name: " + self.fname)
        for key in keylist:
            for value in self[key]:
                try:
                    types = " (" + ", ".join(value[1]["TYPE"]) + ")"
                except KeyError:
                    types = ""
                line = key + types + ": " + value[0]
                collector.append(line)
        return "\n".join(collector)

    def _line_helper(self, line):
        collector = list()
        for key in line[1].keys():
            collector.append(key + "=" + ",".join(line[1][key]))
        if collector == list():
            return ""
        else:
            return ";" + ";".join(collector)

    @property
    def vcf(self):  # noqa: ANN201
        """
        Serialize to VCARD as specified in RFC2426,
        if no UID is specified yet, one will be added (as a UID is mandatory
        for carddav as specified in RFC6352
        TODO make shure this random uid is unique
        """
        import random
        import string

        def generate_random_uid():
            """
            Generate a random uid, when random isn't broken, getting a
            random UID from a pool of roughly 10^56 should be good enough
            """
            choice = string.ascii_uppercase + string.digits
            return "".join([random.choice(choice) for _ in range(36)])

        if "UID" not in self:
            self["UID"] = [(generate_random_uid(), dict())]
        collector = list()
        collector.append("BEGIN:VCARD")
        collector.append("VERSION:3.0")
        for key in ["FN", "N"]:
            try:
                collector.append(key + ":" + self[key][0][0])
            except IndexError:  # broken vcard without FN or N
                collector.append(key + ":")
        for prop in self.alt_keys():
            for line in self[prop]:
                types = self._line_helper(line)
                collector.append(prop + types + ":" + line[0])
        collector.append("END:VCARD")
        return "\n".join(collector)
