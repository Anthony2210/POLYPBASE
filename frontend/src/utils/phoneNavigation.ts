import type { PolypbaseIconName } from '../components/PolypbaseIcon';
import type { TranslationKey } from '../i18n';

export type PhoneDestination = 'overview' | 'zones' | 'labels' | 'profile';

type PhoneDestinationItem = {
  kind: 'destination';
  tab: PhoneDestination;
  icon: PolypbaseIconName;
  labelKey: TranslationKey;
};

type PhoneActionItem = {
  kind: 'action';
  action: 'qr';
  icon: PolypbaseIconName;
  labelKey: TranslationKey;
};

export type PhoneNavigationItem = PhoneDestinationItem | PhoneActionItem;

export const PHONE_NAVIGATION_ITEMS: readonly PhoneNavigationItem[] = [
  { kind: 'destination', tab: 'overview', icon: 'overview', labelKey: 'overview' },
  { kind: 'destination', tab: 'zones', icon: 'location', labelKey: 'phoneLocations' },
  { kind: 'action', action: 'qr', icon: 'qr-scan', labelKey: 'phoneQrAction' },
  { kind: 'destination', tab: 'labels', icon: 'label-qr', labelKey: 'labels' },
  { kind: 'destination', tab: 'profile', icon: 'user', labelKey: 'profile' },
];
