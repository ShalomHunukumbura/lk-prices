# lk-prices

What did rice, dhal or coconut cost before the 2022 crisis, and what do they cost now? Every
working day since August 2016 the Central Bank of Sri Lanka has published a
[Daily Price Report](https://www.cbsl.gov.lk/en/statistics/economic-indicators/price-report):
wholesale and retail prices for about 50 food items in the main markets (Pettah, Dambulla,
Narahenpita, Marandagahamula, Peliyagoda and Negombo). The numbers are public but locked in
about 2,450 PDFs. This pipeline turns them into [Delta Lake](https://delta.io/) tables and a
monthly food price index.

**Status:** in progress. Every report from August 2016 to today is loaded, across all three
layouts CBSL has used, and a local timer adds each new one. Gold has a monthly food price
index, monthly prices and market margins. The public page is next.

Food prices at retail roughly tripled in ten years: the index stands at 322 (August 2016 =
100). From October 2021 to October 2022 it rose 88%, between the official food inflation
figures for September 2022 (85.8% national NCPI, 94.9% Colombo CCPI), from nothing but
CBSL's market prices.

Data: Central Bank of Sri Lanka. This project is not affiliated with CBSL.

## How it works

```
discover   listing pages -> bronze/reports (Delta: one row per report link)
fetch      bronze/reports -> bronze/pdf/year=YYYY/*.pdf (as served)
silver     PDFs -> parse -> checks -> MERGE into silver/prices (Delta, by year)
gold       silver -> flags -> monthly prices -> index, margins (Delta, rebuilt each run)
```

- **discover** reads CBSL's listing, newest first. File names change over the years
  (`Daily Price Report - 05 05 2017.pdf`, `price_report_20160801e.pdf`, `…20211013.pdf`,
  `…20261020_e.pdf`), so links come from the listing and are never guessed. A link it can't
  date stops the crawl, so a new naming scheme can't be skipped silently. A daily run stops
  at the first page with nothing new.
- **fetch** downloads PDFs not yet on disk. Each one is flushed to disk before being
  renamed into place, so a power cut can't leave an empty file under the real name.
  Interrupted backfills resume where they stopped. One report (14 September 2022) is served
  as an empty file; it is listed in `bronze.UNAVAILABLE` and not asked for again.
- **parse** reads the price table by position, not as plain text: a blank cell would
  otherwise slide every number after it into the wrong market. Each number is matched to the
  column header above it, and the rice and fish sub-headers rename only the columns they sit
  over. The previous-day column is "Yesterday", or after a weekend or holiday "Last Friday"
  or just "Friday". Each layout has its own reader, and a PDF matching no known layout is an
  error, never a guess. Reports before June 2017 print no year at all, only "12-Aug" over
  the Today column, so the year is the one that puts that day nearest the listing date. A
  market or page still headed with an earlier day wasn't updated that day and is left out.
- **dates** come from inside each PDF. The listing's dates are wrong for a few dozen
  reports (2019 reports titled 2018, old file names reused for later days), so they are only
  kept as `listed_date` to show where they differ.
- **checks** reject a report with too few prices, duplicate rows, unknown units, or retail
  prices below half of wholesale (the sign of a misread column). Rejected reports are listed
  in `silver/rejects` with the reason. Notices of non-publication (markets closed for the
  New Year) and image-only PDFs are listed there too, but don't count as failures.
- **silver** is one row per date, item, market and price type, with CBSL's "yesterday" value
  kept next to today's. It is partitioned by year, because daily partitions would mean
  thousands of tiny files. Loads are a `MERGE`, so loading a report twice changes nothing.
  `rebuild` re-parses everything after a parser change, and the old version stays readable
  through Delta's time travel.

- **flags** mark prices silver keeps as printed but the index shouldn't trust. A price 3
  times its neighbours that goes straight back the next day is a typing slip (coconut oil
  at Rs 570, 85, 570). Each report's "Yesterday" column also repeats the previous
  report's prices: 99.73% of 243,686 agree, which is a check on the parser as much as on
  CBSL. Nine prices in ten years are left out of the index.
- **gold** is rebuilt from silver on every run, and can be rebuilt from any past silver
  version (`lk-prices gold --version N`), so a published number can be reproduced.
  - The **index** is monthly, chain-linked, with equal weight per item (a Jevons index,
    as statistics offices use below the weighting level). Items and markets come and go
    over ten years, so each month is compared with the one before on the series priced
    in both. A daily chain was tried first and drifted: vegetables go "n.a." when they
    are scarce and dear, and each gap lost part of the rise. CBSL also dropped the fish
    and fruit tables from early 2019 to July 2020; those sections are bridged across the
    gap, and the headline isn't.
  - `lk-prices audit` checks the index against each series' average price from one year
    to another. From 2021 to 2025 it is within 10% for every section. Wholesale fruit
    before 2020 is the weakest part: three items, one market.
  - **Margins** are the median retail markup over wholesale, per item, market and
    month. Pettah's fell from about 17% to 11% in 2022, as retailers absorbed part of
    the crisis.

It runs on plain Python with [Polars](https://pola.rs/),
[delta-rs](https://github.com/delta-io/delta-rs) and
[pdfplumber](https://github.com/jsvine/pdfplumber). No Spark, no JVM.

## Run it

```bash
uv sync
uv run lk-prices discover --all   # first time: read the whole listing (~1 hour)
uv run lk-prices fetch            # download every PDF (~8 hours at 10 s apart)
uv run lk-prices silver           # parse into the silver table
uv run lk-prices gold             # build the index, monthly prices and margins
uv run lk-prices audit            # cross-checks, flagged prices, index validation
uv run lk-prices stats            # reports, downloads and silver rows per year
uv run lk-prices run              # the daily job: discover, fetch, silver, gold
uv run pytest
```

## Being polite to CBSL

`cbsl.gov.lk/robots.txt` asks for 10 seconds between requests, and every request waits that
long. CBSL blocks GitHub's runners, so the daily job runs on a local systemd timer (see
`deploy/systemd/`).

## License

Code: MIT. The price data belongs to the Central Bank of Sri Lanka.
