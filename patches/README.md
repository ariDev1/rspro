# libsigrok APPA S3 patch

Apply `libsigrok-appa-s3-timing.patch` to Cymaphore/libsigrok commit
`8aa4dcd2973ae2a0f961afa61e0ecbcb4e3a92dc` using `git apply`.
The installer performs this step automatically in a fresh checkout.

This patch modifies libsigrok code and is licensed under GPL-3.0-or-later,
consistent with the affected upstream files. See [COPYING](COPYING) for the license text.

The timing increase and timeout-result correction are described in
[the investigation](../docs/s3-timing-bug.md). Diagnostic packet dumping
from the original local checkout is intentionally excluded.
