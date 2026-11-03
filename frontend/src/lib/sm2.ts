/**
 * SM2 public-key encryption (GB/T 32918.4), for the Unione platform's password login.
 *
 * The platform does not accept a password, only a ciphertext: its own web client
 * encrypts with `sm-crypto` (`'04' + sm2.doEncrypt(plain, pubKey, 1)`, C1C3C2) and
 * its server decrypts with Hutool's SM2 engine. `crypto.subtle` has no SM2, and the
 * alternative - pulling in `sm-crypto` for one call site - would put a second copy
 * of the same arithmetic in the bundle with no way to check it against the first.
 *
 * So this module carries the encryption itself, mirroring
 * `backend/app/core/sm2.py` line for line: the layout it produces is the one the
 * platform's `C1C3C2` decryptor parses, which the tests hold to a ciphertext
 * produced by that reference implementation.
 *
 * Why in the browser at all: the plaintext must not leave it. The backend relays a
 * ciphertext it cannot read, and only the platform holds the key that opens it.
 *
 * Ciphertext layout is `04 || X1 || Y1 || C3 || C2`, hex-encoded, each 32-byte field
 * fixed-width and `C2` the length of the message. The leading `04` is the
 * uncompressed point marker and part of the wire format.
 *
 * Two notes on style, both forced by the build: `BigInt(...)` rather than `1n`
 * literals, because this project targets ES2017 and TypeScript refuses bigint
 * literals below ES2020; and `!` after the indexed reads below, because
 * `noUncheckedIndexedAccess` treats every element of a typed array as possibly
 * missing.
 */

/** The recommended curve parameters, GB/T 32918.5 (section 5.2.1). */
const P = BigInt("0xfffffffeffffffffffffffffffffffffffffffff00000000ffffffffffffffff");
const A = BigInt("0xfffffffeffffffffffffffffffffffffffffffff00000000fffffffffffffffc");
const B = BigInt("0x28e9fa9e9d9f5e344d5a9e4bcf6509a7f39789f515ab8f92ddbcbd414d940e93");
const N = BigInt("0xfffffffeffffffffffffffffffffffff7203df6b21c6052b53bbf40939d54123");
const GX = BigInt("0x32c4ae2c1f1981195f9904466a39c9948fe30bbff2660be1715a4589334c74c7");
const GY = BigInt("0xbc3736a2f4f6779c59bdcee36b692153d0a9877cc62a474002df32e52139f0a0");

const ZERO = BigInt(0);
const ONE = BigInt(1);
const TWO = BigInt(2);
const THREE = BigInt(3);
const WORD = BigInt(0xffffffff);

/** A key or a value this module refuses to work with. */
export class Sm2Error extends Error {
  constructor(message: string) {
    super(message);
    this.name = "Sm2Error";
  }
}

// --------------------------------------------------------------------------- //
// SM3 (GB/T 32905) - the digest SM2 mixes into C3 and into its key derivation.
// --------------------------------------------------------------------------- //

const SM3_IV = [
  0x7380166f, 0x4914b2b9, 0x172442d7, 0xda8a0600, 0xa96f30bc, 0x163138aa, 0xe38dee4d, 0xb0fb0e4e,
];

function rotl(value: number, bits: number): number {
  const shift = bits & 31;
  return shift === 0 ? value >>> 0 : ((value << shift) | (value >>> (32 - shift))) >>> 0;
}

function p0(value: number): number {
  return (value ^ rotl(value, 9) ^ rotl(value, 17)) >>> 0;
}

function p1(value: number): number {
  return (value ^ rotl(value, 15) ^ rotl(value, 23)) >>> 0;
}

function compress(state: number[], block: Uint8Array): void {
  const words = new Uint32Array(68);
  for (let index = 0; index < 16; index += 1) {
    const at = index * 4;
    words[index] =
      ((block[at]! << 24) | (block[at + 1]! << 16) | (block[at + 2]! << 8) | block[at + 3]!) >>>
      0;
  }
  for (let index = 16; index < 68; index += 1) {
    const mixed = (words[index - 16]! ^ words[index - 9]! ^ rotl(words[index - 3]!, 15)) >>> 0;
    words[index] = (p1(mixed) ^ rotl(words[index - 13]!, 7) ^ words[index - 6]!) >>> 0;
  }

  let a = state[0]!;
  let b = state[1]!;
  let c = state[2]!;
  let d = state[3]!;
  let e = state[4]!;
  let f = state[5]!;
  let g = state[6]!;
  let h = state[7]!;

  for (let index = 0; index < 64; index += 1) {
    const word = words[index]!;
    const expanded = (word ^ words[index + 4]!) >>> 0;
    let ff: number;
    let gg: number;
    let constant: number;
    if (index < 16) {
      ff = (a ^ b ^ c) >>> 0;
      gg = (e ^ f ^ g) >>> 0;
      constant = 0x79cc4519;
    } else {
      ff = ((a & b) | (a & c) | (b & c)) >>> 0;
      gg = ((e & f) | (~e & g)) >>> 0;
      constant = 0x7a879d8a;
    }
    const ss1 = rotl((rotl(a, 12) + e + rotl(constant, index)) >>> 0, 7);
    const ss2 = (ss1 ^ rotl(a, 12)) >>> 0;
    const tt1 = (ff + d + ss2 + expanded) >>> 0;
    const tt2 = (gg + h + ss1 + word) >>> 0;

    d = c;
    c = rotl(b, 9);
    b = a;
    a = tt1;
    h = g;
    g = rotl(f, 19);
    f = e;
    e = p0(tt2);
  }

  state[0] = (state[0]! ^ a) >>> 0;
  state[1] = (state[1]! ^ b) >>> 0;
  state[2] = (state[2]! ^ c) >>> 0;
  state[3] = (state[3]! ^ d) >>> 0;
  state[4] = (state[4]! ^ e) >>> 0;
  state[5] = (state[5]! ^ f) >>> 0;
  state[6] = (state[6]! ^ g) >>> 0;
  state[7] = (state[7]! ^ h) >>> 0;
}

/** The 32-byte SM3 digest of `data`. */
export function sm3(data: Uint8Array): Uint8Array {
  const padded = Math.ceil((data.length + 9) / 64) * 64;
  const message = new Uint8Array(padded);
  message.set(data);
  message[data.length] = 0x80;
  // The length field is 64 bits wide; a password never reaches 2^32 bits, so the
  // high word is written as the value rather than assumed to be zero.
  const bits = data.length * 8;
  const view = new DataView(message.buffer);
  view.setUint32(padded - 8, Math.floor(bits / 0x100000000));
  view.setUint32(padded - 4, bits >>> 0);

  const state = [...SM3_IV];
  for (let offset = 0; offset < padded; offset += 64) {
    compress(state, message.subarray(offset, offset + 64));
  }

  const digest = new Uint8Array(32);
  const out = new DataView(digest.buffer);
  state.forEach((word, index) => out.setUint32(index * 4, word));
  return digest;
}

// --------------------------------------------------------------------------- //
// Curve arithmetic. Affine, as the reference implementation is: one short
// password per sign-in makes the constant a Jacobian ladder would save worth less
// than arithmetic that can be read against the standard.
// --------------------------------------------------------------------------- //

type Point = { x: bigint; y: bigint };

function mod(value: bigint): bigint {
  const rest = value % P;
  return rest < ZERO ? rest + P : rest;
}

function powMod(base: bigint, exponent: bigint, modulus: bigint): bigint {
  let result = ONE;
  let factor = base % modulus;
  let power = exponent;
  while (power > ZERO) {
    if ((power & ONE) === ONE) result = (result * factor) % modulus;
    factor = (factor * factor) % modulus;
    power >>= ONE;
  }
  return result;
}

function inverse(value: bigint): bigint {
  return powMod(mod(value), P - TWO, P);
}

function add(left: Point | null, right: Point | null): Point | null {
  if (left === null) return right;
  if (right === null) return left;
  if (left.x === right.x) {
    if (mod(left.y + right.y) === ZERO) return null;
    return double(left);
  }
  const slope = mod((right.y - left.y) * inverse(right.x - left.x));
  const x = mod(slope * slope - left.x - right.x);
  return { x, y: mod(slope * (left.x - x) - left.y) };
}

function double(point: Point): Point | null {
  if (point.y === ZERO) return null;
  const slope = mod((THREE * point.x * point.x + A) * inverse(TWO * point.y));
  const x = mod(slope * slope - TWO * point.x);
  return { x, y: mod(slope * (point.x - x) - point.y) };
}

function multiply(scalar: bigint, point: Point): Point | null {
  let result: Point | null = null;
  let addend: Point | null = point;
  let remaining = scalar;
  while (remaining > ZERO) {
    if ((remaining & ONE) === ONE) result = add(result, addend);
    addend = addend === null ? null : double(addend);
    remaining >>= ONE;
  }
  return result;
}

function onCurve(point: Point): boolean {
  return mod(point.y * point.y - (point.x * point.x * point.x + A * point.x + B)) === ZERO;
}

function parsePublicKey(publicKeyHex: string): Point {
  let raw = publicKeyHex.trim().toLowerCase().replace(/\s+/g, "");
  if (raw.startsWith("04")) raw = raw.slice(2);
  if (!/^[0-9a-f]+$/.test(raw) || raw.length !== 128) {
    throw new Sm2Error("The platform's public key is not 64 bytes of hex");
  }
  const point = { x: BigInt(`0x${raw.slice(0, 64)}`), y: BigInt(`0x${raw.slice(64)}`) };
  if (!onCurve(point)) throw new Sm2Error("The platform's public key is not on the curve");
  return point;
}

function kdf(source: Uint8Array, length: number): Uint8Array {
  const out = new Uint8Array(length);
  let counter = 1;
  let written = 0;
  while (written < length) {
    const input = new Uint8Array(source.length + 4);
    input.set(source);
    new DataView(input.buffer).setUint32(source.length, counter);
    const digest = sm3(input);
    const take = Math.min(digest.length, length - written);
    out.set(digest.subarray(0, take), written);
    written += take;
    counter += 1;
  }
  return out;
}

function toHex(value: bigint): string {
  return value.toString(16).padStart(64, "0");
}

/** Write a 32-byte field big-endian at `offset`. */
function writeField(view: DataView, offset: number, value: bigint): void {
  for (let word = 0; word < 8; word += 1) {
    const shift = BigInt((7 - word) * 32);
    view.setUint32(offset + word * 4, Number((value >> shift) & WORD));
  }
}

function randomScalar(): bigint {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let value = ZERO;
  for (const byte of bytes) value = (value << BigInt(8)) | BigInt(byte);
  return (value % (N - ONE)) + ONE;
}

/** Options that exist for the tests, which need a ciphertext that never changes. */
export interface Sm2EncryptOptions {
  /** The ephemeral scalar `k`; a real call draws one from the CSPRNG. */
  scalar?: bigint;
}

/**
 * Encrypt `plaintext` for `publicKeyHex`, returning hex `04||C1||C3||C2`.
 *
 * Throws `Sm2Error` for a key that is not a point on the curve - which is how a
 * deployment with the wrong `UNIONE_SM2_PUB_KEY` fails here rather than as a
 * refused password on the platform.
 */
export function encryptSm2(
  plaintext: string,
  publicKeyHex: string,
  options: Sm2EncryptOptions = {},
): string {
  const message = new TextEncoder().encode(plaintext);
  if (message.length === 0) throw new Sm2Error("Nothing to encrypt");
  // Also the case of an older backend whose config answers no key at all: a missing
  // key is a deployment this form cannot sign in to, not a crash.
  if (!publicKeyHex?.trim()) throw new Sm2Error("No public key to encrypt to");
  const publicKey = parsePublicKey(publicKeyHex);

  for (;;) {
    const k = options.scalar ?? randomScalar();
    if (k < ONE || k > N - ONE) throw new Sm2Error("The ephemeral scalar must be in [1, n-1]");

    const c1 = multiply(k, { x: GX, y: GY });
    const shared = multiply(k, publicKey);
    if (c1 === null || shared === null) {
      if (options.scalar !== undefined) throw new Sm2Error("The ephemeral scalar is unusable");
      continue;
    }

    const x2 = shared.x;
    const y2 = shared.y;
    // The key-derivation input is the shared point's coordinates, each 32 bytes
    // big-endian: `x2 || y2`, the same bytes the reference implementation feeds it.
    const source = new Uint8Array(64);
    const sourceView = new DataView(source.buffer);
    writeField(sourceView, 0, x2);
    writeField(sourceView, 32, y2);

    const stream = kdf(source, message.length);
    if (!stream.some((byte) => byte !== 0)) {
      // GB/T 32918.4 s.6.1 step A5: an all-zero key stream makes this `k` unusable.
      if (options.scalar !== undefined) throw new Sm2Error("The ephemeral scalar is unusable");
      continue;
    }

    const c2 = new Uint8Array(message.length);
    for (let index = 0; index < message.length; index += 1) {
      c2[index] = message[index]! ^ stream[index]!;
    }

    // C3 is SM3 over `x2 || M || y2` - the two coordinates either side of the
    // message, not the concatenated pair followed by the message.
    const digestInput = new Uint8Array(64 + message.length);
    digestInput.set(source.subarray(0, 32), 0);
    digestInput.set(message, 32);
    digestInput.set(source.subarray(32, 64), 32 + message.length);
    const c3 = sm3(digestInput);

    const hex = (bytes: Uint8Array) =>
      Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    return `04${toHex(c1.x)}${toHex(c1.y)}${hex(c3)}${hex(c2)}`;
  }
}
