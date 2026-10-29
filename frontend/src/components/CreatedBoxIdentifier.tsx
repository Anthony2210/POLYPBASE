import type { Translator } from '../i18n';
import type { BoxDetail } from '../types';

export default function CreatedBoxIdentifier({
  createdBox,
  t,
}: {
  createdBox: BoxDetail;
  t: Translator;
}) {
  return (
    <aside className="created-box-identifier is-created" aria-labelledby="created-box-identifier-title">
      <p className="created-box-identifier-title" id="created-box-identifier-title">
        {t('createBoxSuccess')}
      </p>
      <p className="created-box-identifier-value" aria-live="polite">
        <span className="created-box-identifier-code is-confirmed">{createdBox.global_code}</span>
      </p>
    </aside>
  );
}
