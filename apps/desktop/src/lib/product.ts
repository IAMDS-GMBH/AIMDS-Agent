/**
 * The product name end users see in the renderer (AIS-750).
 *
 * One place for the visible name, so the rename (AIS-476) changes it here
 * only. The packaged `productName` in package.json (window titles, About
 * panel, asset names) still says "Hermes" until the rename bridge's second
 * step ships; the internal identity (userData, keychain, AppUserModelID)
 * stays "Hermes" for good — see INTERNAL_APP_NAME in electron/main.cjs.
 */
export const PRODUCT_DISPLAY_NAME = 'AIMDS Suite Agent'
