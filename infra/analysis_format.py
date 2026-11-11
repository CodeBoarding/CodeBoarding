"""Constants of the analysis.json layout."""

# The layout the writer emits and the reader accepts; a document without one predates versioning.
ANALYSIS_FORMAT_VERSION = "v1"

# A method id is a BLAKE2b digest of this many bytes, base64url-encoded: 6 bytes give 8 characters, unpadded.
SYMBOL_ID_BYTES = 6

# A record whose compact JSON fits in this many characters is written on one line.
RECORD_WIDTH = 160
