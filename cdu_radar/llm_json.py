"""Strict model JSON objects; only a single full Markdown fence may be removed."""
from dataclasses import dataclass
import json
import re

PARSER_VERSION = "model-json-wrapper-v1"
ASCII_WHITESPACE = " \t\r\n"
FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```[ \t]*\Z")
FENCE_LINE = re.compile(r"(?m)^[ \t]*(?:`{3,}|~{3,})")


def strict_json(text):
    """The original strict decoder also remains in use for API envelopes."""
    def pairs(items):
        result = {}
        for key,value in items:
            if key in result:raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def constant(_):raise ValueError("Non-finite JSON value")
    return json.loads(text,object_pairs_hook=pairs,parse_constant=constant)


@dataclass(frozen=True)
class ParsedModelJSON:
    value: dict
    method: str
    direct_error_line: int | None = None
    direct_error_column: int | None = None


class ModelJSONError(ValueError):
    def __init__(self, method, detail, *, direct_error=None, inner_error=None, prefix_lines=0):
        # No model content or JSONDecodeError document retained in the exception.
        super().__init__(detail)
        self.method, self.detail = method, detail
        self.direct_error_line = getattr(direct_error,"lineno",None)
        self.direct_error_column = getattr(direct_error,"colno",None)
        error = inner_error if inner_error is not None else direct_error
        self.error_line = getattr(error,"lineno",None)
        if self.error_line is not None and inner_error is not None:self.error_line += prefix_lines + 1
        self.error_column = getattr(error,"colno",None)


def parse_model_json(text):
    """Return an Object and its method; never search prose or repair JSON syntax.

    A fence must use exactly three backticks, lowercase json or no language,
    separate opening/closing lines, and no other fence line in its body.
    Only ASCII whitespace outside the complete block is ignored.
    """
    if not isinstance(text,str):raise ModelJSONError("invalid_json","content_not_string")
    direct_error = None
    try:value = strict_json(text)
    except (ValueError,UnicodeError) as exc:direct_error = exc
    else:
        if not isinstance(value,dict):raise ModelJSONError("invalid_json","top_level_not_object")
        return ParsedModelJSON(value,"direct_json")
    stripped = text.strip(ASCII_WHITESPACE)
    match = FENCE.fullmatch(stripped)
    if match and not FENCE_LINE.search(match["body"]):
        prefix_lines = text[:len(text)-len(text.lstrip(ASCII_WHITESPACE))].count("\n")
        try:value = strict_json(match["body"])
        except (ValueError,UnicodeError) as exc:
            raise ModelJSONError("invalid_json","invalid_fenced_json",direct_error=direct_error,
                                 inner_error=exc,prefix_lines=prefix_lines) from None
        if not isinstance(value,dict):
            raise ModelJSONError("invalid_json","top_level_not_object",direct_error=direct_error) from None
        return ParsedModelJSON(value,"normalized_markdown_fence",getattr(direct_error,"lineno",None),getattr(direct_error,"colno",None))
    method = "unsupported_wrapper" if FENCE_LINE.search(text) else "invalid_json"
    raise ModelJSONError(method,"unsupported_fence_structure" if method=="unsupported_wrapper" else "invalid_direct_json",direct_error=direct_error) from None
