import { layerColor, layerTint } from '../lib/palette'
import { statusTone } from '../lib/runStatus'

/**
 * A model's layer, as a coloured chip.
 *
 * The colour is inline rather than a CSS class per layer: `layerColor()` is
 * already the single source of truth (the lineage nodes read from it too), and
 * seven near-identical CSS rules that must be kept in step with it would be a
 * second one. The chip always shows the layer's *name*, so the colour is a
 * shortcut for people who have learned it, never the only way to read it.
 */
export function LayerChip({ layer }: { layer: string }) {
  return (
    <span
      className="chip"
      style={{ background: layerTint(layer), color: layerColor(layer) }}
    >
      {layer}
    </span>
  )
}

/**
 * A dbt run status. Green pass/success, red error/fail, amber warn/skipped —
 * with the status word itself inside, so colour is never carrying it alone.
 */
export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge--${statusTone(status)}`}>{status}</span>
}
