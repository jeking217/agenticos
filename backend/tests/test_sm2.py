"""The SM2 encryption the Unione bridge sends, held to the standard and to itself.

The platform decrypts with Hutool, which this repository cannot run, so there are
two checks that do bite: the published SM3 vectors (the digest inside every C3),
and a round trip through a decryption written from the same standard - which is
what catches a wrong field order, a wrong coordinate width, or a `04` prefix in the
wrong place, the three ways a ciphertext silently stops being C1C3C2.
"""

import pytest

from app.core import sm2

# Unione's default `secret.soft.sm2keys` pair. The public half is the shipped
# default of `UNIONE_SM2_PUB_KEY`, so a round trip against it is also the check that
# the two halves in this repository agree with each other.
UNIONE_PRIVATE_KEY = "4389c4329a001df855d14ec204d25bf12d4d631bf61bb02d0586a653e199e0da"
UNIONE_PUBLIC_KEY = (
    "04a684d7140aebfc83515a2312dac7ab836f3adc484627971b6936ad7d5575048c"
    "aecf9c7eb07ed4dcee9d04cb3a7b1c514477b646b1ebe8245de215733baa5133"
)


class TestSM3:
    @pytest.mark.parametrize(
        ("message", "digest"),
        [
            (b"abc", "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"),
            (
                b"abcd" * 16,
                "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732",
            ),
            (b"", "1ab21d8355cfa17f8e61194831e81a8f22bec8c728fefb747ed035eb5082aa2b"),
        ],
    )
    def test_gb_t_32905_vectors(self, message: bytes, digest: str) -> None:
        """The published vectors, including the 512-bit one that takes three blocks."""
        assert sm2.sm3(message).hex() == digest

    def test_longer_than_one_block(self) -> None:
        """A message either side of the 64-byte boundary, where padding goes wrong."""
        assert sm2.sm3(b"a" * 64) == sm2.sm3(b"a" * 64)
        assert sm2.sm3(b"a" * 64) != sm2.sm3(b"a" * 65)


class TestKeyPair:
    def test_shipped_public_key_is_the_shipped_private_key(self) -> None:
        """`UNIONE_SM2_PUB_KEY`'s default and the platform's private key describe one pair."""
        derived = sm2._multiply(int(UNIONE_PRIVATE_KEY, 16), (sm2.GX, sm2.GY))
        assert derived is not None
        assert "04%064x%064x" % derived == UNIONE_PUBLIC_KEY


class TestEncrypt:
    def test_ciphertext_round_trips(self) -> None:
        """C1C3C2 in, the same plaintext out - the check a field-order bug fails."""
        ciphertext = sm2.encrypt("Uni@12345".encode(), UNIONE_PUBLIC_KEY)
        assert sm2.decrypt(ciphertext, UNIONE_PRIVATE_KEY) == b"Uni@12345"

    def test_layout_is_04_then_c1_c3_c2(self) -> None:
        """What Hutool and sm-crypto expect to parse: 04, two coordinates, digest, message."""
        plaintext = "correct horse battery staple"
        ciphertext = sm2.encrypt(plaintext.encode(), UNIONE_PUBLIC_KEY)
        assert ciphertext.startswith("04")
        # 1 + 64 + 32 + len(message) bytes
        assert len(ciphertext) == 2 * (97 + len(plaintext.encode()))

    def test_accepts_the_key_without_its_prefix(self) -> None:
        """A key copied from a config as bare X||Y encrypts the same way."""
        assert sm2.encrypt(b"pw", UNIONE_PUBLIC_KEY) != ""
        assert sm2.decrypt(
            sm2.encrypt(b"pw", UNIONE_PUBLIC_KEY[2:]), UNIONE_PRIVATE_KEY
        ) == b"pw"

    def test_every_encryption_differs(self) -> None:
        """A fresh ephemeral scalar each time, or the ciphertext leaks equality of passwords."""
        assert sm2.encrypt(b"pw", UNIONE_PUBLIC_KEY) != sm2.encrypt(b"pw", UNIONE_PUBLIC_KEY)

    def test_refuses_a_key_off_the_curve(self) -> None:
        with pytest.raises(sm2.SM2Error):
            sm2.encrypt(b"pw", "04" + "11" * 64)

    def test_refuses_a_truncated_key(self) -> None:
        with pytest.raises(sm2.SM2Error):
            sm2.encrypt(b"pw", "04a684d7140a")

    def test_refuses_empty_plaintext(self) -> None:
        """Nothing to encrypt is a caller bug, not a ciphertext of length zero."""
        with pytest.raises(sm2.SM2Error):
            sm2.encrypt(b"", UNIONE_PUBLIC_KEY)


class TestDecrypt:
    def test_rejects_a_tampered_c3(self) -> None:
        """The digest is what makes the ciphertext authenticated; flipping it must fail."""
        ciphertext = sm2.encrypt(b"pw", UNIONE_PUBLIC_KEY)
        tampered = ciphertext[:2 + 128] + ("00" if ciphertext[2 + 128 : 2 + 130] != "00" else "01") + ciphertext[2 + 130 :]
        with pytest.raises(sm2.SM2Error):
            sm2.decrypt(tampered, UNIONE_PRIVATE_KEY)

    def test_rejects_a_c1_not_on_the_curve(self) -> None:
        with pytest.raises(sm2.SM2Error):
            sm2.decrypt("04" + "11" * 64 + "22" * 64, UNIONE_PRIVATE_KEY)
