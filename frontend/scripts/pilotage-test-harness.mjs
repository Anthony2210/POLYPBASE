import assert from 'node:assert/strict';
import { createBoxEnvironment } from './create-box-harness.mjs';

// Execute the exported module with real hooks/permissions and inert child surfaces.
export function pilotageHarness(initialProps = {}) {
  const env = createBoxEnvironment();
  const selected = [], searched = [], resolved = [], scrolled = [];
  let retries = 0;
  env.document.getElementById = id => ({ scrollIntoView: options => scrolled.push({ id, options }) });
  const props = {
    activeOrganizationId: 1, boxes: [], boxCollectionStatus: 'ready', exportOptions: null,
    isLoading: false, isCreateBoxOpen: false, isPhoneLayout: false, isOptionsLoading: false,
    profile: null, recentBoxes: [], search: '', searchResults: [], t: key => key,
    onRequestBoxCollection() {}, onRetryBoxCollection() { retries++; },
    onResolveBoxCode(code) { resolved.push(code); return Promise.resolve(null); },
    onCreateBox() { throw new Error('Unexpected box creation'); }, isOperationCurrent: () => true,
    onCreateBoxOpenChange() {}, onRequestOptions() {}, confirmAction: async () => false,
    onSearch(value) { searched.push(value); }, onSelectBox(id) { selected.push(id); },
    ...initialProps,
  };
  const view = env.instance(env.load('../src/components/PilotageView.tsx').default, props);
  function node(type) { return view.nodes.find(node => node.type === type); }
  function manual() {
    if (props.isPhoneLayout && !node('SearchField')) {
      const tab = view.nodes.find(node => node.props.role === 'tab' && node.props.children === 'searchTab');
      assert.ok(tab, 'Missing manual search tab');
      view.click(tab);
    }
  }
  function key(value) {
    const event = { key: value, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    assert.ok(node('SearchField'), 'Missing search field');
    node('SearchField').props.onKeyDown(event);
    view.flush();
    return event;
  }
  return {
    env, view, props, node, manual, key, selected, searched, resolved, scrolled,
    get nodes() { return view.nodes; }, get retries() { return retries; },
  };
}
