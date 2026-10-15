import json
import struct
import zlib

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from mtunifi.protocol import BOOTSTRAP_KEY, HEADER, MAX_PAYLOAD, ProtocolError, decode, encode

MAC = "02:12:34:56:78:9a"
IV = bytes(range(16))
BODY = {"_type": "noop", "interval": 10, "unicode": "síť"}


@pytest.mark.parametrize("mode", ["cbc", "gcm"])
@pytest.mark.parametrize("version", [0, 1])
def test_wire_interoperability_with_independent_crypto(mode, version):
    packet = encode(BODY, MAC, mode=mode, version=version, iv=IV)
    header, payload = packet[:40], packet[40:]
    assert HEADER.unpack(header) == (
        b"TNBU",
        version,
        bytes.fromhex(MAC.replace(":", "")),
        3 if mode == "cbc" else 11,
        IV,
        1,
        len(payload),
    )
    compressed = zlib.compress(json.dumps(BODY, separators=(",", ":")).encode())
    # Independent library and wire construction, rather than only a codec round trip.
    if mode == "cbc":
        expected = AES.new(BOOTSTRAP_KEY, AES.MODE_CBC, IV).encrypt(pad(compressed, 16))
    else:
        cipher = AES.new(BOOTSTRAP_KEY, AES.MODE_GCM, nonce=IV)
        cipher.update(header)
        ciphertext, tag = cipher.encrypt_and_digest(compressed)
        expected = ciphertext + tag
    assert payload == expected
    decoded = decode(header + expected)
    assert decoded.mac == MAC
    assert decoded.body == BODY
    assert decoded.mode == mode


@pytest.mark.parametrize("cut", [0, 4, 39, 40, 60])
def test_truncation(cut):
    with pytest.raises(ProtocolError):
        decode(encode(BODY, MAC)[:cut])


@pytest.mark.parametrize("offset", [0, 7, 8, 15, 20, 35, 39, 45, -1])
def test_gcm_tampering(offset):
    packet = bytearray(encode(BODY, MAC, mode="gcm"))
    packet[offset] ^= 1
    with pytest.raises(ProtocolError):
        decode(bytes(packet))


def test_wrong_key_trailing_bytes_and_snappy():
    packet = encode(BODY, MAC)
    for invalid in (packet + b"x", packet[:14] + b"\x00\x07" + packet[16:]):
        with pytest.raises(ProtocolError):
            decode(invalid)
    with pytest.raises(ProtocolError):
        decode(packet, b"x" * 16)


def test_bounded_inflation():
    compressed = zlib.compress(b" " * (MAX_PAYLOAD + 1))
    encrypted = AES.new(BOOTSTRAP_KEY, AES.MODE_CBC, IV).encrypt(pad(compressed, 16))
    header = HEADER.pack(b"TNBU", 1, bytes.fromhex(MAC.replace(":", "")), 3, IV, 1, len(encrypted))
    with pytest.raises(ProtocolError):
        decode(header + encrypted)


def test_unencrypted_rejected_and_fresh_ivs():
    packet = encode(BODY, MAC)
    with pytest.raises(ProtocolError):
        decode(packet[:14] + struct.pack(">H", 2) + packet[16:])
    assert packet[16:32] != encode(BODY, MAC)[16:32]
