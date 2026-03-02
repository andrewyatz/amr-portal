# Frontend Migration to V2 Schema API

## Overview

The frontend has been updated to consume the new v2 API response format from the backend. The backend now uses the data-loader-etl v2 schema, which simplifies filter group structures and uses explicit `id`/`label` fields instead of overloaded `name` fields.

## Changes

### `types/filters/filtersConfig.ts`
- **`FilterCategoryGroup` renamed to `FilterGroup`** with fields: `id`, `label`, `rank`, `categories` (replaces the old `name` + `categories` structure)
- **`FiltersView.categoryGroups` renamed to `filterGroups`** — a single ranked list replaces the old `categoryGroups` + `otherCategoryGroups` split
- **`FilterCategory`** gains optional fields for new filter types: `filter_type`, `match_type`, `min`, `max`, `query_columns`, `regex`
- **`AMRTableColumn`** gains optional `hidden` field

### `data-provider/apiBackend.ts`
- Removed `OldFiltersView` and `OldFiltersConfig` intermediate types
- Removed the client-side merge hack that combined `categoryGroups` and `otherCategoryGroups` into a single array
- `getFiltersConfig()` now returns the API response directly as `FiltersConfig`

### `state/filtersStore.ts`
- Import changed from `FilterCategoryGroup` to `FilterGroup`
- `filterGroupsForViewMode` reads `filtersView.filterGroups` instead of `filtersView.categoryGroups`
- `activeFilterGroup` computed uses `group.id` for matching instead of `group.name`
- `appliedFiltersCount` iterates `viewConfig.filterGroups` instead of `viewConfig.categoryGroups`
- Removed stale standalone `viewConfig.categoryGroups;` statement

### `components/top-panel/filters-area/filters-area-top.ts`
- Filter group identity comparison uses `group.id` instead of `group.name`
- Click handler passes `group.id` instead of `group.name`
- Display text uses `group.label` instead of `group.name`

## API Contract Change

The `/api/filters-config` response now returns:

```json
{
  "filterViews": [
    {
      "id": 1,
      "name": "Phenotype",
      "url_name": "phenotype",
      "filterGroups": [
        { "id": "pheno_sample", "label": "Sample", "rank": 1, "categories": ["phenotype-biosample_id", ...] }
      ],
      "columns": [...]
    }
  ]
}
```

Key differences from old format:
- `filterGroups` (single ranked list) replaces `categoryGroups` + `otherCategoryGroups`
- Each group has `id` and `label` instead of just `name`
- Groups are ordered by `rank`
