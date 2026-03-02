import { html, css, nothing, LitElement } from 'lit';
import { customElement } from 'lit/decorators.js';
import { SignalWatcher } from '@lit-labs/signals';

import '@ensembl/ensembl-elements-common/components/text-button/text-button.js';

import filtersStore from '../../../state/filtersStore';

@customElement('filters-area-top')
export class FiltersAreaTop extends SignalWatcher(LitElement) {
  static styles = css`
    :host {
      display: grid;
      grid-template-columns: auto 1fr;
      column-gap: 22px;
      align-items: baseline;
    }

    .label {
      font-weight: var(--font-weight-light);
      font-size: 12px;
    }

    .filter-groups-nav {
      display: flex;
      flex-wrap: wrap;
      column-gap: 1rem;
      row-gap: 0.5rem;
    }

    .active {
      --text-button-disabled-color: var(--color-black);
    }
  `;

  #onFiltersGroupSelect = (filtersGroupName: string) => {
    filtersStore.setActiveFilterGroup(filtersGroupName);
  }

  render() {
    const filterGroups = filtersStore.filterGroupsForViewMode.get();
    const activeFilterGroup = filtersStore.activeFilterGroup.get();

    return html`
      <span class="label">
        Filter by
      </span>
      <div class="filter-groups-nav">
        ${filterGroups.map(group => {
          const isActiveGroup = group.id === activeFilterGroup.id;

          return html`
            <ens-text-button
              class=${isActiveGroup ? 'active' : nothing}
              @click=${() => this.#onFiltersGroupSelect(group.id)}
              ?disabled=${isActiveGroup}
            >
              ${group.label}
            </ens-text-button>
         `
        })}
      </div>
    `; 
  }


}