export type Filter = {
  label: string;
  value: string;
};

export type FilterCategory = {
  id: string;
  label: string;
  filter_type: string;
  match_type?: string | null;
  min?: number | null;
  max?: number | null;
  query_columns?: Record<string, string> | null;
  regex?: string | null;
  filters: Filter[];
};

export type FilterCategoriesMap = Record<string, FilterCategory>;

export type FilterGroup = {
  id: string;
  label: string;
  rank: number;
  categories: string[]; // an array of filter category ids
};

/**
 * The fact that the columns of the AMR table are described in this config
 * suggests that it is more than just a filters config; but rather an overall app config
 */
export type AMRTableColumn = {
  id: string | number;
  label: string;
  sortable: boolean;
  rank: number; // <-- describes the order of the columns
  enable_by_default: boolean;
  hidden?: boolean;
};

export type FiltersView = {
  id: number | string;
  name: string;
  url_name: string; // the url identifier of the view
  filterGroups: FilterGroup[]; // ordered by rank
  columns: AMRTableColumn[];
};

export type FiltersConfig = {
  filterCategories: FilterCategoriesMap;
  filterViews: FiltersView[];
};
