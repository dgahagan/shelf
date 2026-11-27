# BN Poland catalogue fixtures

Captured with read-only HTTPS GET on 2026-11-13, without authentication.
Official contract and CC0 licence: <https://data.bn.org.pl/docs/bibs>.
The documented endpoint is `/api/institutions/bibs.json`; `isbn=` is **not**
the documented filter and was observed to return unrelated records.

For each `bn_<isbn13>.json`, GET the following URL with the corresponding
`<isbn10>`:

```text
https://data.bn.org.pl/api/institutions/bibs.json?isbnIssn=<isbn13>+%7C+<isbn10>&boolean=true&limit=5
```

All three returned HTTP 200, `application/json; charset=utf-8`, with no
`Retry-After` or rate-limit headers. No numeric quota is published in these
API docs; use a conservative one-request/second host interval, not a claimed
provider guarantee.

| ISBN-13 | ISBN-10 | Original response SHA-256 | Observed coverage |
| --- | --- | --- | --- |
| 9788375780635 | 8375780634 | `78cc172dc2df31a77fde7091bf1a3f437741947c0d97fd2f558fe217c6475661` | Polish *Ostatnie życzenie*, plus two Ukrainian translations with the queried ISBN only in 020 $z |
| 9788365646156 | 8365646153 | `781af1296b334800341e830489d81effbc97362d8a86cef09a42f5a2a1baad19` | English *Venus in furs*, Polish publication; 041 $a eng, $h ger |
| 9788386075652 | 8386075651 | `e3e04cfb1937b7916dac12e391baddde05be34fba2506524c9f9686e4de0ae61` | Five older records holding only ISBN-10, including multiple reprints reusing it |

These committed files are **projections**, not byte-for-byte bodies: they
retain all returned candidates, `nextPage`, selected original display fields,
MARC leader, and fields 008/020/041/100/245/260/264/300/700. Other fields were
removed; retained values/order are unchanged. The hashes identify the full
original bodies, not the projected files. Further test mutations are synthetic
and occur in test code only.

An ISBN-13-only GET for `isbnIssn=9788386075652&limit=5` returned zero records;
the OR query is necessary for older coverage. `nextPage` is present even for
short results; the adapter never follows it.

The International ISBN Agency's public range message
<https://www.isbn-international.org/export_rangemessage.xml> returned HTTP 200
with registration group `978-83`, agency `Poland` (message serial
`8f4f9d3b-8d6c-414b-bfe8-683004b9cb04`, 2026-11-13). No other Polish prefix
is assumed. These examples demonstrate real coverage, not catalogue
completeness or a way to distinguish reprints sharing an ISBN.
