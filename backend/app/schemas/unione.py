"""Schemas for signing in through the Unione platform.

The form mirrors unione-admin's: an account tab and a phone tab, a graphical captcha
when the platform asks for one, and a one-time ticket for the silent path. Nothing
here carries a value we mint - the password arrives already encrypted for the
platform, and the ticket is the platform's own.
"""

import re

from pydantic import ConfigDict, Field, field_validator

from app.schemas.base import BaseSchema

#: `04`, then C1 (64 bytes), C3 (32 bytes) and at least one byte of C2.
_SM2_CIPHERTEXT = re.compile(r"04[0-9a-fA-F]{194,}")


class UnioneConfigRead(BaseSchema):
    """What the login page needs to know before drawing a form (#2101)."""

    enabled: bool = Field(description="Whether this deployment bridges to the platform at all")
    captcha_enabled: bool = Field(description="Whether the platform's form asks for a captcha")
    local_login_visible: bool = Field(
        description="Whether the local email-and-password form stays beside the platform one"
    )
    sm2_public_key: str = Field(
        default="",
        description=(
            "The platform's SM2 public key, hex, for the form to encrypt the password "
            "with. Empty when the bridge is off. Not a secret: the matching private key "
            "never leaves the platform."
        ),
    )


class UnioneCaptchaRead(BaseSchema):
    """A captcha image and the id the platform expects to be quoted back with the answer.

    The platform hands the id over in a `CAPTCHAID` header and an httpOnly cookie. The
    cookie is useless to us - the answer is checked by the platform, on a request this
    backend makes, so the cookie would have to survive a round trip through a browser
    it was never meant for. The id comes back as a field instead, and the bridge sends
    it as the header the platform reads second.
    """

    captcha_id: str = Field(description="Quote back as `captcha_id` with the answer")
    image: str = Field(description="A `data:` URL the page can put straight into an <img>")


class UnioneLogin(BaseSchema):
    """The platform's own account and password, plus an answer if it wants one.

    `password` is the platform's SM2 ciphertext, not a password: the browser encrypts
    with the key `/auth/unione/config` hands out, so the plaintext never reaches this
    deployment at all. A body that carries one is refused here rather than relayed,
    because a bridge that accepts either would be a bridge whose traffic is sometimes
    readable.

    Whitespace stripping is off, for the reason `DirectoryLogin` has it off: the
    platform compares the decrypted password verbatim, so trimming one that ends in a
    space locks its owner out. The username is trimmed by its own validator.
    """

    model_config = ConfigDict(str_strip_whitespace=False)

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(
        min_length=1,
        max_length=1024,
        description="Hex `04 || C1 || C3 || C2`, encrypted with the platform's public key",
    )
    captcha: str | None = Field(default=None, max_length=16)
    captcha_id: str | None = Field(
        default=None,
        max_length=64,
        description="The id from `/auth/unione/captcha`, sent on as the platform's CAPTCHAID",
    )

    @field_validator("username")
    @classmethod
    def trimmed(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("A username is required")
        return stripped

    @field_validator("password")
    @classmethod
    def encrypted(cls, value: str) -> str:
        """Refuse a password that arrived in the clear (#2101).

        `04 || X1 || Y1 || C3 || C2` is 96 fixed bytes plus one byte of message, so
        nothing shorter than 196 hex characters can be a ciphertext. The error says
        what to send instead, because the caller that gets this is a client this
        deployment did not write.
        """
        raw = value.strip()
        if len(raw) % 2 or not _SM2_CIPHERTEXT.fullmatch(raw):
            raise ValueError(
                "The password must be SM2-encrypted hex (04 || C1 || C3 || C2), "
                "encrypted with the key from /auth/unione/config"
            )
        return raw


class UnioneSmsLogin(BaseSchema):
    """A phone number and the code the platform texted to it."""

    phone: str = Field(min_length=4, max_length=32)
    captcha: str = Field(min_length=1, max_length=16, description="The SMS code")

    @field_validator("phone")
    @classmethod
    def trimmed(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("A phone number is required")
        return stripped


class UnioneSmsCaptchaRequest(BaseSchema):
    """Ask the platform to text a sign-in code to this number."""

    phone: str = Field(min_length=4, max_length=32)

    @field_validator("phone")
    @classmethod
    def trimmed(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("A phone number is required")
        return stripped


class UnioneTicketLogin(BaseSchema):
    """A `tk-` ticket the platform issued for the session its user already has.

    Accepted as `accessToken`, the name the platform uses on the URL it redirects to,
    so the handoff is one string end to end and no client has to translate it.
    """

    access_token: str = Field(
        alias="accessToken", min_length=1, max_length=256, description="A `tk-` ticket"
    )

    @field_validator("access_token")
    @classmethod
    def prefixed(cls, value: str) -> str:
        ticket = value.strip()
        if not ticket.startswith("tk-"):
            raise ValueError("A one-time ticket starts with tk-")
        return ticket
