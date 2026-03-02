import { Signal } from 'signal-polyfill';

import type { FiltersConfig, FiltersView, FilterGroup, AMRTableColumn } from '../types/filters/filtersConfig';

export type SelectedFilter = {
  category: string;
  value: string;
};
export type SelectedFiltersForViewModes = Record<string, SelectedFilter[]>;

export type FiltersUpdatePayload = {
  category: string;
  value: string;
  isSelected: boolean;
};


const viewMode = new Signal.State<FiltersView['id'] | null>(null);
const setViewMode = (viewId: FiltersView['id']) => {
  viewMode.set(viewId);
};

const filtersConfig = new Signal.State<FiltersConfig | null>(null);
const setFiltersConfig = (config: FiltersConfig) => filtersConfig.set(config);

const amrTableColumnsMap = new Signal.Computed(() => {
  const viewConfig = currentViewConfig.get();

  if (!viewConfig) {
    return null;
  }

  const columns = viewConfig.columns;
  const columnsMap: Record<string, AMRTableColumn> = {};
  for (const column of columns) {
    columnsMap[column.id] = column;
  }
  return columnsMap;
});

const selectedFilters = new Signal.State<SelectedFiltersForViewModes>({});
const updateSelectedFilters = (payload: FiltersUpdatePayload) => {
  if (!viewMode.get()) {
    // this function should not be called if the view mode has not been set
    throw new Error('Selected filters can only be updated if view mode is set');
  }
  const { category, value: filterValue, isSelected } = payload;
  const currentViewMode = viewMode.get() as string;
  const storedFiltersForAllModes = selectedFilters.get();
  const storedFilters = storedFiltersForAllModes[currentViewMode];

  let updatedFilters: typeof storedFilters;

  if (isSelected) {
    const filter = { category: payload.category, value: payload.value };
    updatedFilters = storedFilters ? [...storedFilters, filter] : [filter];
  } else {
    updatedFilters = storedFilters.filter(storedFilter =>
      storedFilter.category !== category || storedFilter.value !== filterValue
    );
  }

  selectedFilters.set({
    ...storedFiltersForAllModes,
    [currentViewMode]: updatedFilters
  });
};
const selectedFiltersForViewMode = new Signal.Computed(() => {
  const currentViewMode = viewMode.get();
  const allSelectedFilters = selectedFilters.get();
  if (!currentViewMode) {
    return [];
  }
  return allSelectedFilters[currentViewMode] ?? [];
});
const clearAllFilters = () => {
  selectedFilters.set({}); // clears all filters for all views
}

const filterGroupsForViewMode = new Signal.Computed(() => {
  const filtersView = currentViewConfig.get() as FiltersView;

  return filtersView.filterGroups;
});


const activeFilterGroups = new Signal.State<Record<string, string | null>>({});
const activeFilterGroup = new Signal.Computed<FilterGroup>(() => {
  const currentFiltersView = currentViewConfig.get() as FiltersView;
  const currentActiveFilterGroupIds: Record<string, string | null> = activeFilterGroups.get();
  const currentActiveFilterGroupId = currentActiveFilterGroupIds[currentFiltersView.id];

  if (currentActiveFilterGroupId) {
    return currentFiltersView.filterGroups.find(group => group.id === currentActiveFilterGroupId) as FilterGroup;
  } else {
    return currentFiltersView.filterGroups[0];
  }
});
const setActiveFilterGroup = (filterGroupId: string | null) => {
  const currentViewMode = viewMode.get() as string;
  const currentActiveFilterGroups = activeFilterGroups.get();

  activeFilterGroups.set({
    ...currentActiveFilterGroups,
    [currentViewMode]: filterGroupId
  });
}

const currentViewConfig = new Signal.Computed(() => {
  const currentViewMode = viewMode.get();
  const filtersConfigValue = filtersConfig.get() as FiltersConfig;

  return filtersConfigValue.filterViews.find(view => view.id === currentViewMode) ?? null;
});

const appliedFiltersCount = new Signal.Computed<number>(() => {
  const currentViewMode = viewMode.get();
  const viewConfig = currentViewConfig.get();
  const filtersForViewMode = selectedFiltersForViewMode.get();

  if (!currentViewMode || !viewConfig) {
    return 0;
  }

  const primaryFilterCategoryIds = viewConfig.filterGroups.reduce((ids, group) => {
    return ids.concat(...group.categories)
  }, [] as string[]);

  let count = 0;
  for (const filter of filtersForViewMode) {
    if (primaryFilterCategoryIds.includes(filter.category)) {
      count++;
    }
  }

  return count;
});


const store = {
  viewMode,
  setViewMode,
  filtersConfig,
  setFiltersConfig,
  selectedFilters,
  selectedFiltersForViewMode,
  updateSelectedFilters,
  clearAllFilters,
  appliedFiltersCount,
  filterGroupsForViewMode,
  activeFilterGroups,
  activeFilterGroup,
  setActiveFilterGroup,
  amrTableColumnsMap
};


export default store;