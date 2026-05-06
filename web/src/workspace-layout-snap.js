/**
 * Description:
 *   Provide lightweight layout-guided snap helpers for the FAITH Dockview shell.
 *
 * Requirements:
 *   - Snap persisted split sizes to tidy dashboard-like increments where the layout
 *     clearly uses percentage or ratio-based sizing.
 *   - Leave unknown layout payload fields untouched so Dockview features such as
 *     docking, tab grouping, floating, and restore remain intact.
 *   - Preserve the overall sibling total after snapping so restored layouts do not
 *     drift over time.
 */

const RATIO_COLLECTION_MINIMUM = 0.95;
const RATIO_COLLECTION_MAXIMUM = 1.05;
const RATIO_SNAP_STEP = 0.05;
const RATIO_MINIMUM_SIZE = 0.15;
const RATIO_MAXIMUM_SIZE = 0.85;
const PERCENT_COLLECTION_MINIMUM = 95;
const PERCENT_COLLECTION_MAXIMUM = 105;
const PERCENT_SNAP_STEP = 5;
const PERCENT_MINIMUM_SIZE = 15;
const PERCENT_MAXIMUM_SIZE = 85;
const NORMALIZED_COLLECTION_KEYS = Object.freeze(["content", "children", "views", "panels"]);

/**
 * Description:
 *   Clamp one numeric value inside an inclusive range.
 *
 * Requirements:
 *   - Preserve the original value when it already fits inside the range.
 *
 * @param {number} value Numeric value to clamp.
 * @param {number} minimum Inclusive lower bound.
 * @param {number} maximum Inclusive upper bound.
 * @returns {number} Clamped numeric value.
 */
function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

/**
 * Description:
 *   Round one number to a stable decimal precision.
 *
 * Requirements:
 *   - Avoid noisy floating-point artifacts in persisted layout payloads.
 *
 * @param {number} value Numeric value to round.
 * @param {number} precision Decimal places to keep.
 * @returns {number} Rounded numeric value.
 */
function roundToPrecision(value, precision) {
  return Number(value.toFixed(precision));
}

/**
 * Description:
 *   Resolve the snap configuration for one sibling-size collection.
 *
 * Requirements:
 *   - Treat totals close to `1.0` as ratio-based Dockview collections.
 *   - Treat totals close to `100` as percentage-based collections.
 *   - Return `null` when the collection shape is not recognised safely.
 *
 * @param {number} total Combined sibling-size total.
 * @returns {object|null} Snap configuration, or `null` when no safe snap rule applies.
 */
function getSnapConfiguration(total) {
  if (total >= RATIO_COLLECTION_MINIMUM && total <= RATIO_COLLECTION_MAXIMUM) {
    return {
      step: RATIO_SNAP_STEP,
      minimum: RATIO_MINIMUM_SIZE,
      maximum: RATIO_MAXIMUM_SIZE,
      precision: 4,
    };
  }
  if (total >= PERCENT_COLLECTION_MINIMUM && total <= PERCENT_COLLECTION_MAXIMUM) {
    return {
      step: PERCENT_SNAP_STEP,
      minimum: PERCENT_MINIMUM_SIZE,
      maximum: PERCENT_MAXIMUM_SIZE,
      precision: 2,
    };
  }
  return null;
}

/**
 * Description:
 *   Snap one numeric size value to the configured increment.
 *
 * Requirements:
 *   - Clamp the snapped result inside the configured minimum and maximum.
 *
 * @param {number} value Numeric size value to snap.
 * @param {object} configuration Snap configuration resolved for the sibling collection.
 * @returns {number} Snapped size value.
 */
function snapSizeValue(value, configuration) {
  const snapped = Math.round(value / configuration.step) * configuration.step;
  return roundToPrecision(
    clamp(snapped, configuration.minimum, configuration.maximum),
    configuration.precision,
  );
}

/**
 * Description:
 *   Snap a sibling collection of sized layout nodes while preserving its total.
 *
 * Requirements:
 *   - Snap all but the last sized sibling to the configured grid increment.
 *   - Assign the remaining total to the last sized sibling so persistence remains stable.
 *   - Leave collections unchanged when they do not clearly match a safe snap model.
 *
 * @param {object[]} collection Sibling layout nodes that may carry `size` fields.
 * @returns {object[]} Updated sibling collection.
 */
function normalizeSizedCollection(collection) {
  const sizedIndexes = collection.reduce(function collectSizedIndexes(indexes, item, index) {
    if (typeof item.size === "number" && Number.isFinite(item.size)) {
      indexes.push(index);
    }
    return indexes;
  }, []);

  if (sizedIndexes.length < 2) {
    return collection;
  }

  const total = sizedIndexes.reduce(function sumSizes(sum, index) {
    return sum + collection[index].size;
  }, 0);
  const configuration = getSnapConfiguration(total);
  if (!configuration) {
    return collection;
  }

  let usedTotal = 0;
  return collection.map(function normalizeSizedItem(item, index) {
    if (!sizedIndexes.includes(index)) {
      return item;
    }
    const isLastSizedItem = index === sizedIndexes[sizedIndexes.length - 1];
    const nextSize = isLastSizedItem
      ? roundToPrecision(total - usedTotal, configuration.precision)
      : snapSizeValue(item.size, configuration);
    usedTotal += nextSize;
    return {
      ...item,
      size: nextSize,
    };
  });
}

/**
 * Description:
 *   Recursively normalize one persisted Dockview or fallback layout payload.
 *
 * Requirements:
 *   - Recurse through known container-child collection keys used by FAITH layout payloads.
 *   - Preserve unknown object fields untouched.
 *   - Prefer tidy snapped sizes over arbitrary fractional drift when the layout exposes
 *     clear sibling-size collections.
 *
 * @param {any} layoutNode Persisted layout payload or subtree.
 * @returns {any} Normalized layout payload.
 */
function normalizeLayoutForPersistence(layoutNode) {
  if (Array.isArray(layoutNode)) {
    const normalizedItems = layoutNode.map(normalizeLayoutForPersistence);
    return normalizeSizedCollection(normalizedItems);
  }

  if (!layoutNode || typeof layoutNode !== "object") {
    return layoutNode;
  }

  const normalizedNode = { ...layoutNode };
  Object.keys(normalizedNode).forEach(function normalizeChildValue(key) {
    const value = normalizedNode[key];
    if (Array.isArray(value)) {
      if (NORMALIZED_COLLECTION_KEYS.includes(key)) {
        normalizedNode[key] = normalizeLayoutForPersistence(value);
        return;
      }
      normalizedNode[key] = value.map(normalizeLayoutForPersistence);
      return;
    }
    if (value && typeof value === "object") {
      normalizedNode[key] = normalizeLayoutForPersistence(value);
    }
  });
  return normalizedNode;
}

module.exports = {
  normalizeLayoutForPersistence,
};
