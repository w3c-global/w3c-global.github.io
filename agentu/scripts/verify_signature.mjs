// Offline cryptographic primitive. The caller supplies a separately trusted key.
import { constants, createHash, createPublicKey, verify } from "node:crypto";

function decode(value) {
  if (typeof value !== "string" || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value))
    throw new Error("Invalid base64 encoding.");
  return Buffer.from(value, "base64");
}

try {
  let input = "";
  for await (const part of process.stdin) {
    input += part;
    if (input.length > 16384) throw new Error("Signature input is too large.");
  }
  const data = JSON.parse(input);
  const publicBytes = decode(data.public_key_spki_base64);
  const key = createPublicKey({ key: publicBytes, format: "der", type: "spki" });
  if (key.asymmetricKeyType !== "rsa" || key.asymmetricKeyDetails?.modulusLength !== 3072)
    throw new Error("Expected a trusted RSA-3072 public key.");
  const message = decode(data.message_base64), signature = decode(data.signature_base64);
  if (!message.length || message.length > 4096 || signature.length !== 384)
    throw new Error("Invalid message or signature length.");
  const valid = verify("sha256", message, { key, padding: constants.RSA_PKCS1_PSS_PADDING, saltLength: 32 }, signature);
  if (!valid) throw new Error("Signature does not match the trusted public key.");
  process.stdout.write(JSON.stringify({ signature_valid: true, public_key_sha256: createHash("sha256").update(publicBytes).digest("hex") }));
} catch (error) {
  process.stderr.write("Signature verification failed: " + error.message + "\n");
  process.exitCode = 1;
}
