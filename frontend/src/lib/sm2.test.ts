import { describe, expect, it } from "vitest";

import { encryptSm2, Sm2Error, sm3 } from "@/lib/sm2";

/**
 * The platform's own key pair, the one `backend/.env` ships as a default. The
 * ciphertext below was produced by `backend/app/core/sm2.py` with the ephemeral
 * scalar pinned to 1 - the same value `scalar` takes here - so a match means this
 * implementation produces the bytes the platform's `C1C3C2` decryptor parses.
 */
const PUBLIC_KEY =
  "04a684d7140aebfc83515a2312dac7ab836f3adc484627971b6936ad7d5575048c" +
  "aecf9c7eb07ed4dcee9d04cb3a7b1c514477b646b1ebe8245de215733baa5133";

const VECTOR =
  "0432c4ae2c1f1981195f9904466a39c9948fe30bbff2660be1715a4589334c74c7" +
  "bc3736a2f4f6779c59bdcee36b692153d0a9877cc62a474002df32e52139f0a0" +
  "89c0fe68f0d074563dfd27135e5d8ee2c78589d1b51638bb9b20243bf11e80" +
  "04e4156cab8ac74a343a";

const hex = (text: string) => new TextEncoder().encode(text);

describe("SM3", () => {
  it("matches the standard's vector for abc", () => {
    expect(Array.from(sm3(hex("abc")), (byte) => byte.toString(16).padStart(2, "0")).join("")).toBe(
      "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0",
    );
  });

  it("matches the standard's vector for the empty message", () => {
    expect(Array.from(sm3(hex("")), (byte) => byte.toString(16).padStart(2, "0")).join("")).toBe(
      "1ab21d8355cfa17f8e61194831e81a8f22bec8c728fefb747ed035eb5082aa2b",
    );
  });

  it("matches the standard's 512-bit vector, which crosses a block boundary", () => {
    // 64 bytes: the padding, the length field and the second block all matter.
    const message = hex("abcd".repeat(16));
    expect(Array.from(sm3(message), (byte) => byte.toString(16).padStart(2, "0")).join("")).toBe(
      "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732",
    );
  });
});

describe("encryptSm2", () => {
  it("produces the ciphertext the reference implementation produces", () => {
    expect(encryptSm2("Uni@12345", PUBLIC_KEY, { scalar: BigInt(1) })).toBe(VECTOR);
  });

  it("lays the ciphertext out as 04 then C1, C3, C2", () => {
    const ciphertext = encryptSm2("Uni@12345", PUBLIC_KEY, { scalar: BigInt(1) });
    // 1 marker + 64 C1 + 32 C3 + the message, in hex.
    expect(ciphertext).toHaveLength(2 + 128 + 64 + "Uni@12345".length * 2);
    expect(ciphertext.startsWith("04")).toBe(true);
    expect(ciphertext).toMatch(/^[0-9a-f]+$/);
  });

  it("draws a different ephemeral scalar every time", () => {
    expect(encryptSm2("Uni@12345", PUBLIC_KEY)).not.toBe(
      encryptSm2("Uni@12345", PUBLIC_KEY),
    );
  });

  it("takes the key with or without its 04 prefix", () => {
    expect(encryptSm2("pw", PUBLIC_KEY.slice(2), { scalar: BigInt(1) })).toBe(
      encryptSm2("pw", PUBLIC_KEY, { scalar: BigInt(1) }),
    );
  });

  it("refuses a key that is not a point on the curve", () => {
    expect(() => encryptSm2("pw", `04${"11".repeat(64)}`)).toThrow(Sm2Error);
  });

  it("refuses a key that is not 64 bytes", () => {
    expect(() => encryptSm2("pw", "04deadbeef")).toThrow(Sm2Error);
  });

  it("refuses to encrypt nothing, and refuses to encrypt to nothing", () => {
    expect(() => encryptSm2("", PUBLIC_KEY)).toThrow(Sm2Error);
    expect(() => encryptSm2("pw", "  ")).toThrow(Sm2Error);
  });
});
