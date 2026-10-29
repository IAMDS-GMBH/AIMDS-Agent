/**
 * The product name end users see in the installer and updater (AIS-755).
 *
 * Mirrors apps/desktop/src/lib/product.ts; apps/desktop/src/lib/product.test.ts
 * pins every copy to the same literal. Only visible text uses it: the bundle
 * (productName "Hermes"), asset names (Hermes-Setup), paths and the `hermes`
 * command stay the internal identity (AIS-476).
 */
export const PRODUCT_DISPLAY_NAME = 'AIMDS Suite Agent'
