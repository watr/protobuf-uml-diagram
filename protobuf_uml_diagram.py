#!/usr/bin/env python

# Copyright 2019 Bruno P. Kinoshita
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generate UML diagrams with graphviz from Protobuf compiled Python modules."""

import logging
from importlib import import_module
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Union

import click
from google.protobuf.descriptor_pb2 import FieldDescriptorProto
from graphviz import Source

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

Text = Union[str, bytes]


# https://github.com/pallets/click/issues/405#issuecomment-470812067
class PathPath(click.Path):
    """A Click path argument that returns a pathlib Path, not a string"""

    def convert(self, value: Text, param: Text, ctx) -> Path:
        """Convert a text parameter into a ``Path`` object.
        :param value: parameter value
        :type value: Text
        :param param: parameter name
        :type param: Text
        :param ctx: context
        :type ctx: object
        :return: a ``Path`` object
        :rtype: Path
        """
        return Path(super().convert(value, param, ctx))


# -- mappings

class Mappings:
    types: dict = {}
    type_mapping: dict = {}
    message_mapping: dict = {}


def _get_message_mapping(types: dict) -> dict:
    """
    Return a mapping with the type as key, and the index number.
    :param types: a dictionary of types with the type name, and the message type
    :type types: dict
    :return: message mapping
    :rtype: dict
    """
    message_mapping = {}
    entry_index = 2  # based on the links found, they normally start with 2?
    for _type, message in types.items():
        message_mapping[_type] = entry_index
        entry_index += 1
    return message_mapping


def _build_mappings(proto_file, mappings=None) -> Mappings:
    """Build the mappings for the diagram.
    """
    if mappings is None:
        mappings = Mappings()
    # a mapping with values such as 1: 'double', 9: 'string', etc.
    # to find the text value of a type
    mappings.type_mapping.update(
        {number: text.lower().replace("type_", "") for text, number in FieldDescriptorProto.Type.items()})

    # our compiled type actually includes .DESCRIPTOR where we can find
    # introspection data
    mappings.types.update(proto_file.DESCRIPTOR.message_types_by_name)

    mappings.message_mapping.update(_get_message_mapping(mappings.types))

    for _dep in proto_file.DESCRIPTOR.dependencies:
        _build_mappings(_module(_dep.name), mappings)
    return mappings


# -- UML diagram

def _get_uml_template(*, types: dict, type_mapping: dict, message_mapping: dict) -> str:
    """
    Return the graphviz dot template for a UML class diagram.
    :param types: protobuf types with indexes
    :param type_mapping: a mapping for the protobuf type indexes and the type text
    :param message_mapping: a dict with which messages were linked, for the relationships
    :return: UML template
    :rtype: str
    """
    relationships = []
    classes = []

    uml_template = """
        digraph "Protobuf UML class diagram" {
            fontname = "Bitstream Vera Sans"
            fontsize = 8

            node [
                fontname = "Bitstream Vera Sans"
                fontsize = 8
                shape = "record"
                style=filled
                fillcolor=gray95
            ]

            edge [
                fontname = "Bitstream Vera Sans"
                fontsize = 8

            ]

    CLASSES

    RELATIONSHIPS
        }
        """

    entry_index = 2
    for _type, message in types.items():
        type_template_text = StringIO()
        type_template_text.write(f"""    {entry_index}[label = "{{{_type}|""")
        fields = []
        for _field in message.fields:
            message_type = _field.message_type
            field_type = type_mapping[_field.type]  # this will be 'message' if referencing another protobuf message

            if message_type:
                this_node = message_mapping[_type]
                that_node = message_mapping[message_type.name]
                relationships.append(f"    {this_node}->{that_node}")
                field_type = message_type.name  # so we replace the 'message' token by the actual name

            fields.append(f"+ {_field.name}:{field_type}")

        # add fields
        type_template_text.write("\\n".join(fields))
        type_template_text.write("}\"]\n")
        entry_index += 1
        classes.append(type_template_text.getvalue())

        type_template_text.close()

    uml_template = uml_template.replace("CLASSES", "\n".join(classes))
    uml_template = uml_template.replace("RELATIONSHIPS", "\n".join(relationships))
    return uml_template


# -- Protobuf Python module load

def _module(proto: str) -> ModuleType:
    """
    Given a protobuf file location, it will replace slashes by dots, drop the
    .proto and append _pb2.

    This works for the current version of Protobuf, and loads this way the
    Protobuf compiled Python module.
    :param proto:
    :return: Protobuf compiled Python module
    :rtype: ModuleType
    """
    return import_module(proto.replace(".proto", "_pb2").replace("/", "."))


# -- Diagram builder

class Diagram:
    """A diagram builder."""

    _proto_module: ModuleType = None
    _rendered_filename: str = None

    def from_file(self, proto_file: str):
        if not proto_file:
            raise ValueError("Missing proto file!")
        self._proto_module = _module(proto_file)
        logger.info(f"Imported: {proto_file}")
        return self

    def to_file(self, output: Path):
        if not output:
            raise ValueError("Missing output location!")
        uml_file = Path(self._proto_module.__file__).stem
        self._rendered_filename = str(output.joinpath(uml_file))
        return self

    def build(self, file_format="png"):
        if not self._proto_module:
            raise ValueError("No Protobuf Python module!")
        if not self._rendered_filename:
            raise ValueError("No output location!")

        mappings = _build_mappings(self._proto_module)

        uml_template = _get_uml_template(
            types=mappings.types,
            type_mapping=mappings.type_mapping,
            message_mapping=mappings.message_mapping)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("UML template:")
            logger.debug(uml_template)

        src = Source(uml_template)
        src.format = file_format
        logger.info(f"Writing PNG diagram to {self._rendered_filename}.png")
        src.render(filename=self._rendered_filename, view=False, cleanup=True)


# -- main method

@click.command()
@click.option('--proto', required=True, help='Compiled Python proto module (e.g. some.package.ws_compiled_pb2).')
@click.option('--output', type=PathPath(file_okay=False), required=True, help='Output directory.')
def main(proto: str, output: Path) -> None:
    Diagram() \
        .from_file(proto) \
        .to_file(output) \
        .build()


if __name__ == '__main__':
    main()
