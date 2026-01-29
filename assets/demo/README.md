# Demo video thumbnail

The main README uses a local thumbnail so the demo video preview displays reliably on GitHub (Loom’s CDN is often blocked or flaky there).

**To add or refresh the thumbnail**, from the project root run:

```bash
curl -sL -o assets/demo/thumbnail.gif "https://cdn.loom.com/sessions/thumbnails/018c20b00b84470da28c89616f870a76-with-play.gif"
```

Then commit `assets/demo/thumbnail.gif`. The README will show this image and link to the Loom video.
