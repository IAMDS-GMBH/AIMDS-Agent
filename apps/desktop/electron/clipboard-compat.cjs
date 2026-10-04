'use strict'

// Electron 44 rebuilt the clipboard module on the W3C Clipboard API (AIS-443):
// read/write are async, and readImage/writeImage are gone in favour of
// ClipboardItems keyed by MIME type. These helpers keep the call sites short
// and also work on the pre-44 API (tests, older dev checkouts).

function hasAsyncApi(clipboard) {
  return typeof clipboard.writeImage !== 'function' && typeof clipboard.read === 'function'
}

async function writeClipboardText(clipboard, text) {
  await clipboard.writeText(String(text ?? ''))
}

// `image` is a NativeImage; it is written as PNG.
async function writeClipboardImage(clipboard, ClipboardItem, image) {
  if (!hasAsyncApi(clipboard)) {
    clipboard.writeImage(image)

    return
  }

  const png = image.toPNG()

  await clipboard.write([new ClipboardItem({ 'image/png': new Blob([png], { type: 'image/png' }) })])
}

// The first image on the clipboard as PNG bytes, or null. Non-PNG images
// (JPEG/TIFF from some apps) are converted through nativeImage.
async function readClipboardImagePng(clipboard, nativeImage) {
  if (!hasAsyncApi(clipboard)) {
    const image = clipboard.readImage()

    return image && !image.isEmpty() ? image.toPNG() : null
  }

  const items = await clipboard.read()

  for (const item of items || []) {
    const type = (item.types || []).find(t => String(t).startsWith('image/'))

    if (!type) {
      continue
    }

    const blob = await item.getType(type)
    const bytes = Buffer.from(await blob.arrayBuffer())

    if (type === 'image/png') {
      return bytes
    }

    const image = nativeImage.createFromBuffer(bytes)

    return image.isEmpty() ? null : image.toPNG()
  }

  return null
}

module.exports = { readClipboardImagePng, writeClipboardImage, writeClipboardText }
