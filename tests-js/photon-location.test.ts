import { describe, expect, it, vi } from 'vitest'

// The production sidecar is intentionally plain ESM because it runs directly
// under Node, outside the root TypeScript workspaces. A URL-valued import keeps
// that runtime boundary explicit without adding declarations to production.
const locationModuleUrl = new URL(
  '../plugins/platforms/photon/sidecar/location.mjs',
  import.meta.url,
).href

const {
  isIMessageLocationCustom,
  normalizeIMessageLocation,
  sanitizeSharedLocation,
  selectIMessageLocationClient,
} = await import(locationModuleUrl)

const MAPS_CARD = {
  type: 'custom',
  raw: {
    content: {
      balloonBundleId:
        'com.apple.messages.MSMessageExtensionBalloonPlugin:com.apple.Maps.MessagesExtension',
    },
  },
}

function spectrumWithClient(client: object, phone = 'shared') {
  return {
    __internal: {
      platforms: new Map([
        [
          'iMessage',
          {
            client: [{ phone, client }],
            definition: { name: 'iMessage' },
          },
        ],
      ]),
    },
  }
}

describe('Photon iMessage location normalization', () => {
  it('recognizes Apple Maps and Find My balloons only', () => {
    expect(isIMessageLocationCustom(MAPS_CARD)).toBe(true)
    expect(
      isIMessageLocationCustom({
        type: 'custom',
        raw: {
          metadata: {
            balloonBundleId:
              'com.apple.messages.MSMessageExtensionBalloonPlugin:com.apple.findmy.FindMyMessagesApp',
          },
        },
      }),
    ).toBe(true)
    expect(
      isIMessageLocationCustom({
        type: 'custom',
        raw: {
          content: {
            balloonBundleId:
              'com.apple.messages.MSMessageExtensionBalloonPlugin:com.apple.DigitalTouchBalloonProvider',
          },
        },
      }),
    ).toBe(false)
  })

  it('selects the already-authenticated client for the receiving line', () => {
    const expected = { locations: { get: vi.fn() } }
    const app = spectrumWithClient(expected, '+15550001111')
    expect(selectIMessageLocationClient(app, '+15550001111')).toBe(expected)
    expect(selectIMessageLocationClient(app, '+15550002222')).toBe(expected)
  })

  it('returns only allowlisted location fields from a fresh snapshot', async () => {
    const get = vi.fn().mockResolvedValue({
      name: 'Test Place',
      address: 'short',
      longAddress: '1 Example Road',
      latitude: 31.2304,
      longitude: 121.4737,
      locationTimestamp: new Date('2026-08-23T12:00:00Z'),
      locationType: 'live',
      isLocatingInProgress: false,
      privateAccountMetadata: 'must-not-leak',
    })

    const app = spectrumWithClient({ locations: { get } })

    const result = await normalizeIMessageLocation(MAPS_CARD, {
      app,
      phone: 'shared',
      senderId: '+15550003333',
      messageTimestamp: new Date('2026-08-23T12:01:00Z'),
      now: new Date('2026-08-23T12:01:00Z'),
    })

    expect(get).toHaveBeenCalledWith('+15550003333')
    expect(result).toEqual({
      type: 'location',
      source: 'shared-location',
      resolved: true,
      name: 'Test Place',
      address: '1 Example Road',
      latitude: 31.2304,
      longitude: 121.4737,
      locationTimestamp: '2026-08-23T12:00:00.000Z',
      locationType: 'live',
    })
  })

  it('does not present an expired or stale snapshot as the sent place', () => {
    expect(
      sanitizeSharedLocation(
        {
          address: 'Old place',
          latitude: 1,
          longitude: 2,
          locationTimestamp: new Date('2026-08-23T10:00:00Z'),
          isLocatingInProgress: false,
        },
        new Date('2026-08-23T12:00:00Z'),
        new Date('2026-08-23T12:00:00Z'),
      ),
    ).toBeNull()
    expect(
      sanitizeSharedLocation(
        {
          address: 'Expired place',
          expiresAt: new Date('2026-08-23T11:59:00Z'),
          isLocatingInProgress: false,
        },
        new Date('2026-08-23T12:00:00Z'),
        new Date('2026-08-23T12:00:00Z'),
      ),
    ).toBeNull()
  })

  it('recognizes the card even when the location lookup is unavailable', async () => {
    const app = spectrumWithClient({
      locations: { get: vi.fn().mockRejectedValue(new Error('not sharing')) },
    })

    await expect(
      normalizeIMessageLocation(MAPS_CARD, {
        app,
        phone: 'shared',
        senderId: '+15550003333',
      }),
    ).resolves.toEqual({ type: 'location', resolved: false })
  })
})
