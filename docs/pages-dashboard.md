# GitHub Pages dashboard: Phase 3

The static site lives in `site/`. Its only data request is the URL in
`site/config.json`, currently `./data/latest.json`. Relative URLs keep it
working under GitHub Pages' repository subpath. The page can select the exact
next or fifth NSE session, inspect every stock, show realized target closes
once available, and export the visible horizon as CSV. It never calls the local
Python dashboard API or attempts to refresh NSE from a browser.

The Pages workflow uploads **only `site/`** from the public repository. It
does not check out the private live-state repository or copy any model, source
archive, corporate-action review, or raw price dataset. The committed
`site/data/latest.json` is an unavailable-state marker, so the deployed page
does not expose NSE prices or forecasts yet.

## Public snapshot contract

`scripts/build_pages_snapshot.py` reads the verified private store and selects
the latest immutable, prospectively issued forecast. It checks the 50-stock
basket, the origin's verified prices, model/calendar identity, target dates,
and both adopted horizon policies. Its JSON projection contains only fields
the page displays: symbol, origin close, target dates, issued forecast values,
and separately realized actual closes and absolute errors. It never exports
historical feature tables or NSE report bodies.

Without explicit authorization, the builder writes only an unavailable marker:

```bash
python -m scripts.build_pages_snapshot \
  --state-dir ../nifty50-live-state \
  --output /tmp/nifty50-pages/latest.json
```

For a **local, nonpublished** functional preview, build into a temporary
directory with `--publication-authorized`, copy the resulting JSON into a
temporary copy of `site/data/latest.json`, and serve that copy locally. The
flag does not grant publication rights. Do not commit that output or upload
the private repository.

NSE's [terms of use](https://www.nseindia.com/static/nse-terms-of-use) and
[data sharing policy](https://www.nseindia.com/static/market-data/nse-data-policy)
restrict public dissemination and display of market data, including EOD
prices. Before switching this dashboard to real public values, obtain written
permission or use a data source whose terms permit public display of the
required closes and derived forecasts. The current free-stack assumption does
not establish those rights.

## Deployment and endpoint

Pushes that change `site/` trigger `.github/workflows/pages.yml`. The workflow
can also be run manually. GitHub Pages must be configured to use **GitHub
Actions** as its build and deployment source. The public URL is the repository
Pages URL, normally `https://prajjwal-acharya.github.io/nifty50-technical-analysis-regression/`.

When a permitted public JSON endpoint exists, replace `snapshot_url` in
`site/config.json` with its HTTPS URL. A cross-origin endpoint must allow the
Pages origin through CORS. The browser reads the endpoint directly; no API key
can safely be embedded in `config.json` or JavaScript. Phase 4 will connect a
scheduled publisher once the display source is settled.
