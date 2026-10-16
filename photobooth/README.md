# little photo booth

A browser photo booth: it takes any number of photos with your webcam,
then develops them into a strip with a film filter, printed out of a
little booth machine.

- **just me** — one column, 1–10 photos.
- **together ♡** — two people take turns; the strip prints two columns
  side by side.
- **filters** — vintage (sepia), noir, warm film, natural. The filter is
  applied to the captured photos afterwards, so you can switch filters
  once the strip has printed and it re-develops without retaking.
- **download strip** saves a PNG.

Everything runs in the browser; photos are never uploaded.

## Files

| file | what it does |
| --- | --- |
| `index.html` | page structure |
| `style.css` | the booth, machine, and strip styling |
| `app.js` | camera, countdown, capture, filters, strip rendering |

No build step and no dependencies.

## Running it

Browsers only allow camera access on `https://` or `localhost`, so open it
through a server rather than as a file:

```bash
cd photobooth
python3 -m http.server 8000
# then visit http://localhost:8000
```

To host it, put this folder on any static host (GitHub Pages, Netlify,
Vercel, Cloudflare Pages). They all serve over https, so the camera works.
