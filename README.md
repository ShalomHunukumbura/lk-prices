# lk-prices

What does a week of groceries cost in Sri Lanka today, and which supermarket is cheapest? The
official CPI comes out once a month. This pipeline reads supermarket prices every day and
keeps them as [Delta Lake](https://delta.io/) tables, which will feed a daily food price
index.

**Status:** in progress. Glomark is collected daily, about 5,000 products. Keells and
Cargills, unit prices, the index and the public page are next.

## How it works

```
fetch        sitemap -> category pages -> bronze (raw JSON, one file per category per day)
silver       bronze  -> checks -> MERGE into the silver Delta table
```

- **bronze** keeps exactly what the store served, as gzipped JSON under
  `data/bronze/store=<store>/date=<day>/category=<id>.json.gz`. The one exception: Glomark
  lists the email of the staff member responsible for each brand, and that is removed before
  anything is written. Files are never rewritten, so a run that stops halfway picks up where
  it left off.
- **silver** is one typed row per store, product and day in `data/silver/prices`, partitioned
  by date. Loads are a `MERGE` on (store, date, product), so loading a day twice changes
  nothing.
- **checks** stop a load if a price is zero or missing, a promo price is above the shelf price,
  or a product appears twice. A big drop in product count is reported as a warning.
- **rebuild** recreates silver from all of bronze after a parsing change. It overwrites the
  table, so the previous version can still be read with Delta's time travel.

It runs on plain Python with [Polars](https://pola.rs/) and
[delta-rs](https://github.com/delta-io/delta-rs): no Spark, no JVM, and about 130 MB of RAM.

## Run it

```bash
uv sync
uv run lk-prices run        # fetch today, then load silver
uv run lk-prices stats      # rows per store and day
uv run lk-prices rebuild    # rebuild silver from bronze
uv run pytest
```

## Being polite to the stores

One run a day, one request at a time with a pause between them, and a User-Agent that links
back here. Only public product pages are read, as allowed by each store's `robots.txt`.
Prices are as shown online and can differ from prices in the shops.

## License

MIT
