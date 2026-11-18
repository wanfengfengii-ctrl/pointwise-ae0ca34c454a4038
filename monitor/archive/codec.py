from fractions import Fraction
import hashlib
import json


class CodecError(ValueError):
    pass


def encode_value(value):
    if isinstance(value, Fraction):
        return {"$fraction": [value.numerator, value.denominator]}
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, (list, tuple)):
        return [encode_value(v) for v in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        if "$fraction" in value:
            raise CodecError("reserved encoding key")
        return {k: encode_value(v) for k, v in value.items()}
    raise CodecError("unsupported archive value")


def decode_value(value):
    if isinstance(value, dict):
        if "$fraction" in value:
            pair = value.get("$fraction")
            if set(value) != {"$fraction"} or not isinstance(pair, list) or len(pair) != 2:
                raise CodecError("invalid fraction tag")
            if any(type(x) is not int for x in pair) or pair[1] <= 0:
                raise CodecError("invalid fraction components")
            result = Fraction(*pair)
            if [result.numerator, result.denominator] != pair:
                raise CodecError("fraction is not canonical")
            return result
        return {k: decode_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_value(v) for v in value]
    if value is None or type(value) in (str, int, bool):
        return value
    raise CodecError("unsupported encoded value")


def canonical_json(value):
    return json.dumps(encode_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def payload_digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def loads(text):
    try:
        return decode_value(json.loads(text))
    except (ValueError, TypeError) as exc:
        raise CodecError("invalid archive JSON") from exc
