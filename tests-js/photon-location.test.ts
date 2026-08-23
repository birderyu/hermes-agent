import { describe, expect, it, vi } from 'vitest'

const locationModuleUrl = new URL(
  '../plugins/platforms/photon/sidecar/location.mjs',
  import.meta.url,
).href

const {
  isIMessageLocationCustom,
  normalizeIMessageLocation,
  sanitizeMiniAppLocation,
} = await import(locationModuleUrl)

const MAPS_CARD = {
  type: 'custom',
  raw: {
    imessage_type: 'mini-app',
    balloonBundleId:
      'com.apple.messages.MSMessageExtensionBalloonPlugin:com.apple.Maps.MessagesExtension',
    miniApp: {
      extensionBundleId: 'com.apple.Maps.MessagesExtension',
      appName: 'Maps',
      live: false,
      url: 'https://maps.apple.com/?address=1%20Example%20Road&ll=31.2304,121.4737&q=Test%20Place',
      layout: {
        caption: 'Test Place',
        subcaption: '1 Example Road',
        summary: 'Test Place, 1 Example Road',
      },
    },
  },
}

describe('Photon iMessage location normalization', () => {
  it('recognizes decoded Apple Maps and Find My mini-app cards only', () => {
    expect(isIMessageLocationCustom(MAPS_CARD)).toBe(true)
    expect(
      isIMessageLocationCustom({
        type: 'custom',
        raw: {
          miniApp: {
            extensionBundleId: 'com.apple.findmy.FindMyMessagesApp',
          },
        },
      }),
    ).toBe(true)
    expect(
      isIMessageLocationCustom({
        type: 'custom',
        raw: {
          miniApp: {
            extensionBundleId: 'com.apple.DigitalTouchBalloonProvider',
          },
        },
      }),
    ).toBe(false)
  })

  it('extracts the exact place, address, coordinates, URL, and visible text', async () => {
    await expect(normalizeIMessageLocation(MAPS_CARD)).resolves.toEqual({
      type: 'location',
      source: 'map-card',
      resolved: true,
      name: 'Test Place',
      address: '1 Example Road',
      latitude: 31.2304,
      longitude: 121.4737,
      url: 'https://maps.apple.com/?address=1%20Example%20Road&ll=31.2304,121.4737&q=Test%20Place',
      cardText: ['Test Place', '1 Example Road', 'Test Place, 1 Example Road'],
    })
  })

  it('uses visible card text when the extension omits a URL', () => {
    expect(
      sanitizeMiniAppLocation({
        type: 'custom',
        raw: {
          miniApp: {
            extensionBundleId: 'com.apple.Maps.MessagesExtension',
            layout: { caption: 'Library', subcaption: '100 Main Street' },
          },
        },
      }),
    ).toEqual({
      type: 'location',
      source: 'map-card',
      resolved: true,
      name: 'Library',
      address: '100 Main Street',
      cardText: ['Library', '100 Main Street'],
    })
  })

  it('resolves a Find My balloon through the sender snapshot on lowercase providers', async () => {
    const legacyCard = {
      type: 'custom',
      raw: {
        balloonBundleId:
          'com.apple.messages.MSMessageExtensionBalloonPlugin:0000000000:com.apple.findmy.FindMyMessagesApp',
      },
    }
    const get = vi.fn().mockResolvedValue({
      address: 'short address',
      longAddress: '1 Example Road',
      isLocatingInProgress: false,
      locationType: 'legacy',
      privateMetadata: 'must-not-forward',
    })
    await expect(
      normalizeIMessageLocation(legacyCard, {
        app: {
          __internal: {
            platforms: new Map([
              [
                'imessage',
                {
                  client: [
                    {
                      phone: 'shared',
                      client: { locations: { get } },
                    },
                  ],
                  definition: { name: 'imessage' },
                },
              ],
            ]),
          },
        },
        phone: 'shared',
        senderId: '+15550003333',
        settleMs: 0,
      }),
    ).resolves.toEqual({
      type: 'location',
      source: 'shared-location',
      resolved: true,
      address: '1 Example Road',
    })
    expect(get).toHaveBeenCalledWith('+15550003333')
  })

  it('keeps a recognized card unresolved when the sender snapshot is unavailable', async () => {
    const get = vi.fn().mockRejectedValue(new Error('not available'))
    await expect(
      normalizeIMessageLocation(
        {
          type: 'custom',
          raw: {
            balloonBundleId:
              'com.apple.messages.MSMessageExtensionBalloonPlugin:com.apple.findmy.FindMyMessagesApp',
          },
        },
        {
          app: {
            __internal: {
              platforms: new Map([
                [
                  'imessage',
                  {
                    client: [
                      { phone: 'shared', client: { locations: { get } } },
                    ],
                  },
                ],
              ]),
            },
          },
          phone: 'shared',
          senderId: '+15550003333',
          settleMs: 0,
        },
      ),
    ).resolves.toEqual({ type: 'location', resolved: false })
  })

  it('rejects non-map custom URLs while keeping visible text', () => {
    expect(
      sanitizeMiniAppLocation({
        type: 'custom',
        raw: {
          miniApp: {
            extensionBundleId: 'com.apple.Maps.MessagesExtension',
            url: 'javascript:alert(1)',
            layout: { caption: 'Visible place' },
          },
        },
      }),
    ).toEqual({
      type: 'location',
      source: 'map-card',
      resolved: true,
      name: 'Visible place',
      cardText: ['Visible place'],
    })
  })
})
