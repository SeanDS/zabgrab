"""Zabbix models and item spec parsing."""

from __future__ import annotations

import pytest

from zabgrab.model.choice import Choice
from zabgrab.model.item import ItemSpec
from zabgrab.model.itemspec import format_item_spec, parse_item_spec
from zabgrab.model.zabbix import ValueType


class TestValueType:
    @pytest.mark.parametrize(
        ("value_type", "numeric"),
        [
            (ValueType.FLOAT, True),
            (ValueType.UNSIGNED, True),
            (ValueType.CHAR, False),
            (ValueType.LOG, False),
            (ValueType.TEXT, False),
            (ValueType.BINARY, False),
        ],
    )
    def test_numeric(self, value_type: ValueType, numeric: bool) -> None:
        assert value_type.numeric is numeric

    def test_labels(self) -> None:
        assert [v.label for v in ValueType] == [
            "float",
            "char",
            "log",
            "unsigned",
            "text",
            "binary",
        ]


class TestParseItemSpec:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("12345", ItemSpec(itemid=12345)),
            (" 7 ", ItemSpec(itemid=7)),
            ("Particles *", ItemSpec(name="Particles *")),
            ("host=demo1", ItemSpec(host="demo1")),
            (
                "host=demo1,name=Particles *",
                ItemSpec(host="demo1", name="Particles *"),
            ),
            (
                "host = demo1 , name = CPU load",
                ItemSpec(host="demo1", name="CPU load"),
            ),
            (
                "key=net.if.in[eth0,bytes],host=h",
                ItemSpec(key="net.if.in[eth0,bytes]", host="h"),
            ),
            ("name=a,b,c", ItemSpec(name="a,b,c")),
            ("key=f[a,mode=x]", ItemSpec(key="f[a,mode=x]")),
            ("tag=area:lab", ItemSpec(tag="area:lab")),
            ("itemid=99", ItemSpec(itemid=99)),
        ],
    )
    def test_valid(self, text: str, expected: ItemSpec) -> None:
        assert parse_item_spec(text) == expected

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("", "Empty item"),
            ("   ", "Empty item"),
            ("hots=demo1", "Can't parse 'hots=demo1'"),
            ("name=x,hots=y", "Unknown field 'hots'.*did you mean 'host'"),
            ("key=a, nmae=b", "Unknown field 'nmae'.*did you mean 'name'"),
            ("host=a,host=b", "given twice"),
            ("itemid=5,key=x", "can't be combined"),
            ("itemid=abc", "Invalid item"),
            ("name=", "Invalid item"),
        ],
    )
    def test_invalid(self, text: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            parse_item_spec(text)


class TestFormatItemSpec:
    @pytest.mark.parametrize(
        "text",
        [
            "12345",
            "name=Particles *",
            "host=demo1,name=Particles *",
            "host=h,key=net.if.in[eth0,bytes],tag=a:b",
        ],
    )
    def test_round_trip(self, text: str) -> None:
        assert format_item_spec(parse_item_spec(text)) == text

    def test_host_choice_has_no_cli_form(self) -> None:
        spec = ItemSpec(host=Choice[str](choices=["a", "b"]), name="x")
        with pytest.raises(ValueError, match="choice of hosts"):
            format_item_spec(spec)
