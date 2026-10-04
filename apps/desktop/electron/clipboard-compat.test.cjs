'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const { readClipboardImagePng, writeClipboardImage, writeClipboardText } = require('./clipboard-compat.cjs')

const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 1, 2, 3])

function fakeImage(bytes = PNG, empty = false) {
  return { isEmpty: () => empty, toPNG: () => bytes }
}

class FakeClipboardItem {
  constructor(data) {
    this.data = data
    this.types = Object.keys(data)
  }

  async getType(type) {
    return this.data[type]
  }
}

function asyncClipboard(items = []) {
  const written = []

  return {
    items,
    read: async () => items,
    write: async next => {
      written.push(...next)
    },
    writeText: async text => written.push(text),
    written
  }
}

test('Electron 44 API: images go out as an image/png ClipboardItem', async () => {
  const clipboard = asyncClipboard()

  await writeClipboardImage(clipboard, FakeClipboardItem, fakeImage())

  const [item] = clipboard.written
  assert.deepEqual(item.types, ['image/png'])
  assert.deepEqual(Buffer.from(await item.data['image/png'].arrayBuffer()), PNG)
})

test('Electron 44 API: the first clipboard image is read back as PNG', async () => {
  const clipboard = asyncClipboard([
    new FakeClipboardItem({ 'text/plain': new Blob(['x']) }),
    new FakeClipboardItem({ 'image/png': new Blob([PNG], { type: 'image/png' }) })
  ])

  assert.deepEqual(await readClipboardImagePng(clipboard, null), PNG)
})

test('Electron 44 API: other image types are converted through nativeImage', async () => {
  const jpeg = Buffer.from([0xff, 0xd8, 0xff])
  const clipboard = asyncClipboard([new FakeClipboardItem({ 'image/jpeg': new Blob([jpeg]) })])
  const seen = []
  const nativeImage = {
    createFromBuffer: bytes => {
      seen.push(bytes)

      return fakeImage(PNG)
    }
  }

  assert.deepEqual(await readClipboardImagePng(clipboard, nativeImage), PNG)
  assert.deepEqual(seen[0], jpeg)
})

test('Electron 44 API: no image on the clipboard gives null', async () => {
  const clipboard = asyncClipboard([new FakeClipboardItem({ 'text/plain': new Blob(['x']) })])

  assert.equal(await readClipboardImagePng(clipboard, null), null)
})

test('pre-44 API still works', async () => {
  const calls = []
  const clipboard = {
    readImage: () => fakeImage(PNG),
    writeImage: image => calls.push(['image', image.toPNG()]),
    writeText: text => calls.push(['text', text])
  }

  await writeClipboardImage(clipboard, FakeClipboardItem, fakeImage())
  await writeClipboardText(clipboard, 'hi')
  assert.deepEqual(calls, [
    ['image', PNG],
    ['text', 'hi']
  ])
  assert.deepEqual(await readClipboardImagePng(clipboard, null), PNG)
  assert.equal(await readClipboardImagePng({ ...clipboard, readImage: () => fakeImage(PNG, true) }, null), null)
})
