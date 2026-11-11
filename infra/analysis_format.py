"""Constants of the analysis.json layout."""

# The layout the writer emits and the reader accepts; a document without one predates versioning.
ANALYSIS_FORMAT_VERSION = "v1"

# A method id is a BLAKE2b digest of this many bytes, base64url-encoded: 9 bytes give 12 characters, unpadded.
# Why: with 10M methods the chance that any two ids collide is about 1 in 94 million; with 6 bytes it is 16%.
SYMBOL_ID_BYTES = 9

# A record whose compact JSON fits in this many characters is written on one line.
RECORD_WIDTH = 160
