import { CirclePause, CirclePlay, GitFork, Pencil, Route } from 'lucide-react';
import {
  lazy,
  type FormEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

import { ApiError, ApiResourceCancelledError } from '../api/client';
import { getBoxStatusPresentation } from '../boxStatus';
import type { Language, TranslationKey, Translator } from '../i18n';
import type { EditableMeasurement } from '../types';
import BoxLifecycleModal, {
  type BoxLifecycleAction,
  type BoxLifecycleSubmission,
} from './BoxLifecycleModal';

// A measurement handed over by the history so the box sheet can open with it
// already filled in.
type HistoryMeasurementPrefill = EditableMeasurement;
import type { BoxInsightTab } from './BoxInsights';
import type { ConfirmActionOptions } from './ConfirmActionModal';
import DetailBackButton from './DetailBackButton';
import InfoPill from './InfoPill';
import Metric from './Metric';
import LatestMovementBanner from './LatestMovementBanner';
import StepperButton from './StepperButton';
import { RowActionMenu, type RowActionMenuItem } from './RowActionMenu';
import MeasurementSaveButton from './MeasurementSaveButton';
import MoveBoxModal from './MoveBoxModal';
import PageLoader from './PageLoader';
import PolypbaseIcon from './PolypbaseIcon';
import QuickCountButtons from './QuickCountButtons';
import QrLabel from './QrLabel';
import QrLabelModal from './QrLabelModal';
import SubcultureModal from './SubcultureModal';
import { useIsDesktopApp } from '../hooks/useIsDesktopApp';
import { useIsPhoneLayout } from '../hooks/useIsPhoneLayout';
import type {
  BiologicalMeasurement,
  BoxActivatePayload,

  BoxDeactivatePayload,
  BoxDetail,

  BoxItem,
  BoxLineage,
  BoxMovement,
  BoxMovementCorrectionPayload,
  BoxMovePayload,

  LineageGraph,

  SubculturePayload,
  SubcultureResult,
  ThermalZone,
  UserProfile,

} from '../types';
import { userCanWriteLabData } from '../utils/profilePermissions';
import { isBoxLocationChangedError } from '../utils/boxErrors';
import {
  findMeasurementForWeek,
  formatMeasurementCount,
  getMeasurementEditorMode,
  getMeasurementFormValues,
  isMeasurementEditWindowExpired,
  isMeasurementPayloadUnchanged,
  isMeasurementWeekConflict,
  type MeasurementPayloadValues as MeasurementPayload,
} from '../utils/boxMeasurement';
import { formatDisplayDate } from '../utils/dateFormat';
import { formatSalinity, formatTemperature } from '../utils/environmentFormat';
import { getErrorMessage } from '../utils/errors';
import { formatBiologicalSalinity, stepBiologicalSalinity } from '../utils/biologicalSalinity';
import { triggerHaptic } from '../utils/haptics';
import { getBoxInsightsLabels } from '../utils/presentationLabels';
import { buildQrLabelItem, getBoxQrImageUrl, getBoxScanUrl, type QrLabelItem } from '../utils/qrLabels';

const BoxInsights = lazy(() => import('./BoxInsights'));
const MeasurementHistoryModal = lazy(() =>
  import('./BoxInsights').then((module) => ({ default: module.MeasurementHistoryModal })),
);

// Biological controls step by a tenth while preserving the field's hundredths.
// The field starts on the control
// salinity of the box's zone -- the environment it is known to sit in -- and the
// technician overrides it when the refractometer disagrees. It stays empty while
// the zone has no salinity set, rather than storing a value nobody measured.
const SALINITY_STEP = 0.1;

type TFunction = Translator;
type ConfirmAction = (options: ConfirmActionOptions) => Promise<boolean>;

export default function BoxPage({
  box,
  zones,
  profile,
  language,
  qrLabelSelection,
  isLoading,
  onCreateMeasurement,
  isOperationCurrent,
  onUpdateMeasurement,
  onRefreshMeasurementState,
  onCorrectMovement,
  onRefreshMovementState,
  onCreateSubculture,
  onMoveBox,
  onDeactivateBox,
  onReactivateBox,
  onLoadLineageGraph,
  measurementPrefill,
  onMeasurementPrefillConsumed,
  onOpenBox,
  onOpenZone,
  onAddQrLabel,
  onBack,
  onOpenQrLabelSelection,
  confirmAction,
  t,
}: {
  box: BoxItem | BoxDetail | null;
  zones: ThermalZone[];
  profile: UserProfile | null;
  language: Language;
  qrLabelSelection: QrLabelItem[];
  isLoading: boolean;
  onCreateMeasurement: (boxId: number, payload: MeasurementPayload) => Promise<BiologicalMeasurement>;
  isOperationCurrent: () => boolean;
  onUpdateMeasurement: (
    boxId: number,
    measurementId: number,
    payload: MeasurementPayload,
  ) => Promise<BiologicalMeasurement>;
  onRefreshMeasurementState: (boxId: number) => Promise<BoxDetail>;
  onCorrectMovement: (boxId: number, movementId: number, payload: BoxMovementCorrectionPayload) => Promise<void>;
  onRefreshMovementState: (boxId: number) => Promise<BoxDetail>;
  onCreateSubculture: (boxId: number, payload: SubculturePayload) => Promise<SubcultureResult>;
  onMoveBox: (boxId: number, payload: BoxMovePayload) => Promise<BoxDetail>;
  onDeactivateBox: (boxId: number, payload: BoxDeactivatePayload) => Promise<void>;
  onReactivateBox: (boxId: number, payload: BoxActivatePayload) => Promise<void>;
  onLoadLineageGraph: (boxId: number) => Promise<LineageGraph>;
  measurementPrefill: HistoryMeasurementPrefill | null;
  onMeasurementPrefillConsumed: () => void;
  onOpenBox: (boxId: number, globalCode: string) => void;
  onOpenZone: (zoneId: number) => void;
  onAddQrLabel: (label: QrLabelItem) => void;
  onBack: () => void;
  onOpenQrLabelSelection: () => void;
  confirmAction: ConfirmAction;
  t: TFunction;
}) {
  const operationLifetimeRef = useRef(true);
  useLayoutEffect(() => {
    operationLifetimeRef.current = true;
    return () => { operationLifetimeRef.current = false; };
  }, [box?.id]);
  const defaultSalinity = getDefaultMeasurementSalinity(box, zones);
  const [form, setForm] = useState(() => getInitialMeasurementForm(defaultSalinity));
  const [isSaving, setIsSaving] = useState(false);
  const isDesktopApp = useIsDesktopApp();
  const isPhoneLayout = useIsPhoneLayout();
  const isTabletLayout = !isDesktopApp && !isPhoneLayout;
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [lineageGraph, setLineageGraph] = useState<LineageGraph | null>(null);
  const [isLineageGraphLoading, setIsLineageGraphLoading] = useState(false);
  const [lineageGraphError, setLineageGraphError] = useState<string | null>(null);
  const lineageRequestGenerationRef = useRef(0);
  const lineageRequestPendingRef = useRef(false);
  useEffect(() => () => {
    lineageRequestGenerationRef.current += 1;
    lineageRequestPendingRef.current = false;
  }, [box?.id]);
  const [isMoveOpen, setIsMoveOpen] = useState(false);
  const [isSavingMove, setIsSavingMove] = useState(false);
  const [isChangingBoxStatus, setIsChangingBoxStatus] = useState(false);
  const [lifecycleAction, setLifecycleAction] = useState<BoxLifecycleAction | null>(null);
  const [moveError, setMoveError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [isSubcultureOpen, setIsSubcultureOpen] = useState(false);
  const [isSavingSubculture, setIsSavingSubculture] = useState(false);
  const [isQrLabelOpen, setIsQrLabelOpen] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [editingMeasurementId, setEditingMeasurementId] = useState<number | null>(null);
  const [isMeasurementEditorOpen, setIsMeasurementEditorOpen] = useState(false);
  // Set while the form holds values brought over from the history, so the save
  // button can say "correct" rather than "record": the technician is fixing an
  // existing measurement, not adding one.
  const [isCorrectingFromHistory, setIsCorrectingFromHistory] = useState(false);
  const [subcultureError, setSubcultureError] = useState<string | null>(null);
  const [subcultureSuccess, setSubcultureSuccess] = useState<SubcultureResult | null>(null);
  const [activeInsightTab, setActiveInsightTab] = useState<BoxInsightTab>('measurements');
  const [measurementReferenceDate, setMeasurementReferenceDate] = useState(getTodayDateValue);
  const measurements = box ? getMeasurementHistory(box) : [];
  const canWriteLabData = box
    ? userCanWriteLabData(profile, box.organization.id)
    : false;
  const canCreateMeasurement = box && 'can_create_measurement' in box
    ? box.can_create_measurement
    : false;
  const isBoxActive = box?.status === 'active';
  const weeklyMeasurement = findMeasurementForWeek(measurements, measurementReferenceDate);
  const measurementEditorMode = getMeasurementEditorMode({
    measurement: weeklyMeasurement,
    canCreateMeasurement,
  });
  const editingMeasurement = editingMeasurementId == null
    ? null
    : measurements.find((measurement) => measurement.id === editingMeasurementId) ?? null;
  const canShowMeasurementForm = measurementEditorMode === 'create'
    || (isMeasurementEditorOpen && Boolean(editingMeasurement?.can_edit));
  const isMeasurementFormLocked = editingMeasurementId != null
    && !editingMeasurement?.can_edit;
  // The draft is compared with the persisted measurement through the same
  // normalization the save payload applies, so an untouched form cannot be
  // saved again. This is frontend protection only: the server still decides
  // whether the measurement may be edited at all.
  const persistedMeasurementPayload = editingMeasurement
    ? buildMeasurementPayload(getMeasurementFormValues(editingMeasurement))
    : null;
  const draftMeasurementPayload = parsePositiveInteger(form.polypCount) != null
    && parsePositiveInteger(form.ephyraeCount) != null ? buildMeasurementPayload(form) : null;
  const isMeasurementDraftUnchanged = persistedMeasurementPayload != null && draftMeasurementPayload != null
    && isMeasurementPayloadUnchanged(persistedMeasurementPayload, draftMeasurementPayload);
  const showWeeklyMeasurementSummary = Boolean(weeklyMeasurement)
    && (!isMeasurementEditorOpen || !editingMeasurement?.can_edit);
  const isMeasurementEditorExpanded = Boolean(weeklyMeasurement)
    && isMeasurementEditorOpen
    && Boolean(editingMeasurement?.can_edit);

  useEffect(() => {
    const refreshDate = () => setMeasurementReferenceDate(getTodayDateValue());
    window.addEventListener('focus', refreshDate);
    const interval = window.setInterval(refreshDate, 60_000);
    return () => {
      window.removeEventListener('focus', refreshDate);
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (!box || !weeklyMeasurement?.can_edit || !weeklyMeasurement.edit_deadline) {
      return;
    }
    const delay = Date.parse(weeklyMeasurement.edit_deadline) - Date.now() + 100;
    const timeout = window.setTimeout(
      () => void onRefreshMeasurementState(box.id).catch(() => {
        // A failed capability refresh must not produce an unhandled rejection.
      }),
      Math.max(0, delay),
    );
    return () => window.clearTimeout(timeout);
  }, [box?.id, weeklyMeasurement?.id, weeklyMeasurement?.can_edit, weeklyMeasurement?.edit_deadline]);

  useEffect(() => {
    setForm(getInitialMeasurementForm(defaultSalinity));
    setIsHistoryOpen(false);
    setLineageGraph(null);
    setIsLineageGraphLoading(false);
    setLineageGraphError(null);
    setIsMoveOpen(false);
    setIsSavingMove(false);
    setIsChangingBoxStatus(false);
    setLifecycleAction(null);
    setMoveError(null);
    setStatusError(null);
    setIsSubcultureOpen(false);
    setIsQrLabelOpen(false);
    setSaveError(null);
    setEditingMeasurementId(null);
    setIsMeasurementEditorOpen(false);
    setMeasurementReferenceDate(getTodayDateValue());
    setSubcultureError(null);
    setSubcultureSuccess(null);
    setActiveInsightTab('measurements');
    setIsCorrectingFromHistory(false);
  }, [box?.id]);

  useEffect(() => {
    if (isCorrectingFromHistory) return;

    if (!isMeasurementEditorOpen) {
      setEditingMeasurementId(null);
      if (measurementEditorMode === 'create') {
        setForm(getInitialMeasurementForm(defaultSalinity, measurementReferenceDate));
      }
      return;
    }

    if (weeklyMeasurement?.can_edit) {
      setForm(getMeasurementFormValues(weeklyMeasurement));
      setEditingMeasurementId(weeklyMeasurement.id);
      return;
    }

    setEditingMeasurementId(null);
    setIsMeasurementEditorOpen(false);
  }, [
    box?.id,
    defaultSalinity,
    isCorrectingFromHistory,
    isMeasurementEditorOpen,
    measurementEditorMode,
    measurementReferenceDate,
    weeklyMeasurement?.id,
    weeklyMeasurement?.can_edit,
  ]);

  useEffect(() => {
    if (!isMeasurementEditorOpen || editingMeasurementId == null) return;
    if (editingMeasurement?.can_edit) return;

    if (editingMeasurement) {
      setForm(getMeasurementFormValues(editingMeasurement));
    }
    setEditingMeasurementId(null);
    setIsCorrectingFromHistory(false);
    setIsMeasurementEditorOpen(false);
  }, [editingMeasurement?.id, editingMeasurement?.can_edit, editingMeasurementId, isMeasurementEditorOpen]);

  // The history sends the user here to correct a measurement: fill the form
  // with what was recorded. Declared after the reset above so it runs last and
  // its values survive the box change. Keeping the original date matters: the
  // API stores one measurement per box and week, so saving corrects that
  // measurement instead of adding a second one for the week.
  useEffect(() => {
    if (!measurementPrefill || !box || measurementPrefill.box_id !== box.id) return;
    // The journal carries the values, but only the box sheet's own history
    // carries the server-computed correction capability. Wait for it instead of
    // dropping the correction while the detail is still loading.
    if (!('biological_measurements' in box)) return;

    const target = measurements.find(
      (measurement) => measurement.id === measurementPrefill.id,
    );
    onMeasurementPrefillConsumed();
    if (!target?.can_edit) return;

    setMeasurementReferenceDate(target.measured_on);
    setForm(getMeasurementFormValues(target));
    setEditingMeasurementId(target.id);
    setIsCorrectingFromHistory(true);
    setIsMeasurementEditorOpen(true);
  }, [measurementPrefill, box, measurements]);

  // The zones can finish loading after the sheet is open, and the box can be
  // moved to another zone: seed the salinity once its control value is known.
  // Only an untouched field is filled, so this never overwrites a reading the
  // technician typed, nor the value loaded when correcting a past measurement.
  useEffect(() => {
    if (editingMeasurementId != null || !defaultSalinity) return;
    setForm((current) => (current.salinity ? current : { ...current, salinity: defaultSalinity }));
  }, [defaultSalinity, editingMeasurementId]);

  useEffect(() => {
    if (activeInsightTab !== 'lineage' || !box?.id || lineageGraph || lineageGraphError) return;
    void handleLoadLineageGraph();
  }, [activeInsightTab, box?.id, lineageGraph, lineageGraphError]);

  if (isLoading) {
    return (
      <PageLoader variant="box" label={t('boxSheet')} />
    );
  }

  if (!box) {
    return (
      <section className="box-page empty-box-state">
        <DetailBackButton label={t('back')} onBack={onBack} />
        <h2>{t('boxNotFound')}</h2>
        <p>{t('boxNotFoundText')}</p>
      </section>
    );
  }

  const lastComment = getLatestComment(measurements, box);
  const summaryComment = weeklyMeasurement?.notes?.trim() || lastComment;
  const qr = 'qr_image_url' in box
    ? { imageUrl: getBoxQrImageUrl(box), scanUrl: getBoxScanUrl(box) }
    : null;
  const lineage = getBoxLineage(box);
  const currentZone = getCurrentThermalZone(box, zones);
  const displayDate = getBoxDisplayDate(box, measurements);
  const statusPresentation = getBoxStatusPresentation(box.status, language);
  const canChangeBoxStatus = userCanArchiveBox(profile, box.organization.id);
  const canShowStatusButton = canChangeBoxStatus && ['active', 'inactive'].includes(box.status);
  type BoxAction = 'qr' | 'move' | 'subculture' | 'tracking';
  const boxActions: RowActionMenuItem<BoxAction>[] = [
    ...(qr && canWriteLabData ? [{ action: 'qr' as const, label: t('qrLabelTitle') }] : []),
    ...(canWriteLabData ? [
      { action: 'move' as const, label: t('moveAction') },
      ...(isBoxActive ? [{ action: 'subculture' as const, label: t('subcultureAction') }] : []),
    ] : []),
    ...(canShowStatusButton ? [{
      action: 'tracking' as const,
      label: isChangingBoxStatus ? t('saving') : t(isBoxActive ? 'boxArchiveAction' : 'boxActivateAction'),
      disabled: isChangingBoxStatus,
    }] : []),
  ];
  function dispatchBoxAction(action: BoxAction) {
    const item = boxActions.find((candidate) => candidate.action === action);
    if (!item || item.disabled) return;
    if (action === 'qr') setIsQrLabelOpen(true);
    else if (action === 'move') setIsMoveOpen(true);
    else if (action === 'subculture') {
      setSubcultureError(null);
      setIsSubcultureOpen(true);
    }
    else {
      setStatusError(null);
      setLifecycleAction(isBoxActive ? 'deactivate' : 'reactivate');
    }
  }

  async function saveMeasurement(): Promise<boolean> {
    if (!box || isSaving) return false;

    const targetMeasurement = editingMeasurementId == null
      ? null
      : measurements.find((measurement) => measurement.id === editingMeasurementId) ?? null;
    if (
      (targetMeasurement && !targetMeasurement.can_edit)
      || (!targetMeasurement && measurementEditorMode !== 'create')
    ) {
      return false;
    }

    if (editingMeasurementId != null && isMeasurementDraftUnchanged) return false;

    if (!form.polypCount.trim() || !form.ephyraeCount.trim()) {
      setSaveError(t('measurementCountsRequired'));
      return false;
    }

    if (parsePositiveInteger(form.polypCount) == null || parsePositiveInteger(form.ephyraeCount) == null) {
      setSaveError(t('measurementCountsInvalid'));
      return false;
    }

    setIsSaving(true);
    setSaveError(null);

    const payload = buildMeasurementPayload(form);

    try {
      if (editingMeasurementId != null) {
        await onUpdateMeasurement(box.id, editingMeasurementId, payload);
      } else {
        await onCreateMeasurement(box.id, payload);
      }
      if (!operationLifetimeRef.current || !isOperationCurrent()) return false;
      setEditingMeasurementId(null);
      setIsCorrectingFromHistory(false);
      setIsMeasurementEditorOpen(false);
      triggerHaptic([12, 28, 12]);
      return true;
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent() || requestError instanceof ApiResourceCancelledError) return false;
      setSaveError(getMeasurementSaveError(requestError, t));
      return false;
    } finally {
      if (operationLifetimeRef.current && isOperationCurrent()) setIsSaving(false);
    }
  }


  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isDesktopApp) return;
    void saveMeasurement();
  }

  function openWeeklyMeasurementEditor() {
    if (!weeklyMeasurement?.can_edit) return;
    setForm(getMeasurementFormValues(weeklyMeasurement));
    setEditingMeasurementId(weeklyMeasurement.id);
    setIsCorrectingFromHistory(false);
    setSaveError(null);
    setIsMeasurementEditorOpen(true);
  }

  function cancelMeasurementEdit() {
    if (editingMeasurement) {
      setForm(getMeasurementFormValues(editingMeasurement));
    }
    setEditingMeasurementId(null);
    setIsCorrectingFromHistory(false);
    setSaveError(null);
    setIsMeasurementEditorOpen(false);
  }

  async function handleSubculture(payload: SubculturePayload) {
    if (!box || isSavingSubculture) return;
    const confirmed = await confirmAction({
      title: t('confirmSubcultureTitle'),
      message: t('confirmSubcultureMessage'),
      confirmLabel: t('confirmSubcultureAction'),
      cancelLabel: t('confirmCancel'),
      variant: 'warning',
      details: [
        { label: t('confirmDetailParentBox'), value: box.global_code },
        { label: t('confirmDetailSpecies'), value: box.species.scientific_name },
        { label: t('confirmDetailChildren'), value: payload.children.length },
      ],
    });
    if (!confirmed || !operationLifetimeRef.current || !isOperationCurrent()) return;

    setIsSavingSubculture(true);
    setSubcultureError(null);

    let result: SubcultureResult;
    try {
      result = await onCreateSubculture(box.id, payload);
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      setSubcultureSuccess(result);
      setIsSubcultureOpen(false);
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent() || requestError instanceof ApiResourceCancelledError) return;
      setSubcultureError(getSubcultureSaveError(requestError, t));
      return;
    } finally {
      if (operationLifetimeRef.current && isOperationCurrent()) setIsSavingSubculture(false);
    }

    // Subculture is already committed; the optional lifecycle operation is separate.
    if (result.parent_polyp_count_after === 0 && result.allocated_polyp_count !== null
        && result.allocations.length > 0 && result.allocations.every((allocation) => allocation.allocated_polyps !== null)
        && canChangeBoxStatus) {
      const deactivate = await confirmAction({
        title: t('subcultureDeactivateParentTitle'),
        message: t('subcultureDeactivateParentMessage'),
        confirmLabel: t('boxArchiveAction'),
        cancelLabel: t('subcultureKeepParentActive'),
        details: [{ label: t('confirmDetailParentBox'), value: box.global_code }],
      });
      if (deactivate && operationLifetimeRef.current && isOperationCurrent()) {
        setStatusError(null);
        setLifecycleAction('deactivate');
      }
    }
  }

  async function handleMove(payload: BoxMovePayload) {
    if (!box || isSavingMove) return;
    const targetZone = zones.find((zone) => zone.id === payload.thermal_zone_id) ?? null;
    const confirmed = await confirmAction({
      title: t('confirmMoveTitle'),
      message: t('confirmMoveMessage'),
      confirmLabel: t('confirmMoveAction'),
      cancelLabel: t('confirmCancel'),
      details: [
        { label: t('confirmDetailBox'), value: box.global_code },
        { label: t('confirmDetailCurrentLocation'), value: currentZone?.name ?? '-' },
        { label: t('confirmDetailTargetLocation'), value: targetZone?.name ?? '-' },
      ],
    });
    if (!confirmed || !operationLifetimeRef.current || !isOperationCurrent()) return;

    setIsSavingMove(true);
    setMoveError(null);

    try {
      await onMoveBox(box.id, payload);
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      setIsMoveOpen(false);
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent() || requestError instanceof ApiResourceCancelledError) return;
      setMoveError(getMoveSaveError(requestError, t));
    } finally {
      if (operationLifetimeRef.current && isOperationCurrent()) setIsSavingMove(false);
    }
  }

  async function handleLifecycleSubmit(submission: BoxLifecycleSubmission) {
    if (!box || isChangingBoxStatus) return;
    setIsChangingBoxStatus(true);
    setStatusError(null);

    try {
      if (submission.action === 'reactivate') {
        await onReactivateBox(box.id, submission.payload);
      } else if (submission.action === 'deactivate') {
        await onDeactivateBox(box.id, submission.payload);
      }
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      setLifecycleAction(null);
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent() || requestError instanceof ApiResourceCancelledError) return;
      if (requestError instanceof ApiError && requestError.status === 403) {
        setStatusError(t(submission.action === 'reactivate' ? 'boxActivateForbidden' : 'boxArchiveForbidden'));
      } else {
        setStatusError(getErrorMessage(requestError));
      }
    } finally {
      if (operationLifetimeRef.current && isOperationCurrent()) setIsChangingBoxStatus(false);
    }
  }

  async function handleLoadLineageGraph() {
    if (!box || lineageRequestPendingRef.current) return;
    const generation = ++lineageRequestGenerationRef.current;
    const isCurrent = () => generation === lineageRequestGenerationRef.current && isOperationCurrent();
    lineageRequestPendingRef.current = true;
    setLineageGraph(null);
    setIsLineageGraphLoading(true);
    setLineageGraphError(null);

    try {
      const graph = await onLoadLineageGraph(box.id);
      if (isCurrent()) setLineageGraph(graph);
    } catch (requestError) {
      if (isCurrent() && !(requestError instanceof ApiResourceCancelledError)) {
        setLineageGraphError(getErrorMessage(requestError));
      }
    } finally {
      if (isCurrent()) {
        lineageRequestPendingRef.current = false;
        setIsLineageGraphLoading(false);
      }
    }
  }

  return (
    <section className={canWriteLabData ? 'box-page' : 'box-page is-read-only'}>
      {!isDesktopApp ? <DetailBackButton label={t('back')} onBack={onBack} /> : null}

      <header className={`entity-header entity-header--box box-sheet-hero is-status-${statusPresentation.tone}${isTabletLayout ? ' is-tablet' : isPhoneLayout ? ' is-phone' : ''}`}>
        <div className="entity-header__identity box-sheet-identity">
          <div>
            <p className="box-page-label">{t('boxSheet')}</p>
            <div className="box-code-line">
              <h2 data-box-page-focus-target tabIndex={-1}>{box.global_code}</h2>
            </div>
            <p className="box-species-name">{box.species.scientific_name}</p>
          </div>

          {isDesktopApp ? (
            <div className="box-small-facts">
              <InfoPill
                label={t(displayDate.labelKey)}
                value={displayDate.date ? formatDisplayDate(displayDate.date) : t('noDate')}
              />
            </div>
          ) : null}
        </div>

        <div className="box-header-tools">
          {!isPhoneLayout && qr && canWriteLabData ? (
            <button
              className="box-hero-qr"
              type="button"
              aria-label={`${t('qrLabelTitle')} ${box.global_code}`}
              title={qr.scanUrl}
              onClick={() => dispatchBoxAction('qr')}
            >
              <QrLabel
                altLabel={t('qrCode')}
                item={buildQrLabelItem(box, qr.imageUrl)}
                showMetadata={false}
                variant="trigger"
              />
            </button>
          ) : null}
          {isTabletLayout ? (
            <div className="box-tablet-actions">
              {boxActions.filter((item) => item.action !== 'qr').map((item) => (
                <button
                  key={item.action}
                  className={`icon-button box-compact-action${item.action === 'move' || item.action === 'subculture' ? ' box-compact-action--labeled' : ''}`}
                  type="button"
                  aria-label={item.label}
                  title={item.label}
                  disabled={item.disabled}
                  onClick={() => dispatchBoxAction(item.action)}
                >
                  {item.action === 'move' ? (
                    <>
                      <Route aria-hidden="true" size={20} />
                      <span>{item.label}</span>
                    </>
                  ) : item.action === 'subculture' ? (
                    <>
                      <GitFork className="box-subculture-glyph" aria-hidden="true" size={20} />
                      <span>{item.label}</span>
                    </>
                  )
                      : isBoxActive ? <CirclePause aria-hidden="true" size={36} />
                        : <CirclePlay aria-hidden="true" size={36} />}
                </button>
              ))}
            </div>
          ) : null}
          {isPhoneLayout && boxActions.length > 0 ? (
            <RowActionMenu actions={boxActions} onAction={dispatchBoxAction} ariaLabel={`${t('boxInventoryActions')} ${box.global_code}`} />
          ) : null}
        </div>

        <div className="entity-header__summary box-zone-summary">
          <div className={isDesktopApp ? 'box-summary-metadata' : 'box-summary-metadata is-compact'}>
            {!isDesktopApp ? (
              <InfoPill
                label={t(displayDate.labelKey)}
                value={displayDate.date ? formatDisplayDate(displayDate.date) : t('noDate')}
              />
            ) : null}
          {box.thermal_zone ? (
            <button
              className="info-pill is-strong box-zone-link"
              type="button"
              onClick={() => onOpenZone(box.thermal_zone!.id)}
            >
              <small>{t('zones')}</small>
              <strong>{box.thermal_zone.name}</strong>
            </button>
          ) : (
            <InfoPill label={t('zones')} value={t('noZone')} strong />
          )}
          </div>
          <InfoPill label={t('zoneSalinityShort')} value={formatSalinity(currentZone?.salinity_psu)} />
          {/* Salinity recorded for this box (the last measurement's PSU), shown
              right after the zone reference so both are read side by side. */}
          <InfoPill label={t('boxSalinityShort')} value={formatSalinity(box.latest_salinity_psu)} />
          <InfoPill label={t('temperatureShort')} value={formatTemperature(currentZone?.latest_temperature?.average_temperature_c)} />
        </div>

        {isDesktopApp ? (
          <div className="entity-header__actions box-action-stack">
            {boxActions.filter((item) => item.action !== 'qr').map((item) => (
              <button
                key={item.action}
                className={item.action === 'tracking'
                  ? isBoxActive ? 'archive-box-trigger' : 'activate-box-trigger'
                  : `${item.action}-trigger`}
                type="button"
                disabled={item.disabled}
                onClick={() => dispatchBoxAction(item.action)}
              >
                {item.action === 'tracking' ? (
                  <span className="button-icon-label">
                    {!isChangingBoxStatus ? (
                      <PolypbaseIcon name={isBoxActive ? 'archive' : 'restore'} size={17} />
                    ) : null}
                    {item.label}
                  </span>
                ) : item.label}
              </button>
            ))}
          </div>
        ) : null}
      </header>

      {statusError ? (
        <p className="inline-error box-action-feedback">{statusError}</p>
      ) : null}

      <div className={`box-page-grid${!isBoxActive ? ' is-inactive' : ''}${weeklyMeasurement ? ' has-measurement-module' : ''}`}>
        {(showWeeklyMeasurementSummary || (!weeklyMeasurement && isBoxActive)) ? (
        <section
          className={`last-reading-card${showWeeklyMeasurementSummary ? ' measurement-summary measurement-module' : ''}${showWeeklyMeasurementSummary && weeklyMeasurement?.can_edit ? ' has-edit-capability' : ''}`}
        >
          <div>
            <h2>{t('lastMeasurement')}</h2>
            <span>
              {weeklyMeasurement && showWeeklyMeasurementSummary
                ? formatDisplayDate(weeklyMeasurement.measured_on)
                : box.latest_measurement
                  ? formatDisplayDate(box.latest_measurement.measured_on)
                  : t('noDate')}
            </span>
          </div>
          <Metric
            label={t('polyps')}
            value={formatMeasurementCount(
              weeklyMeasurement && showWeeklyMeasurementSummary
                ? weeklyMeasurement.polyp_count
                : box.latest_measurement?.polyp_count,
            )}
          />
          <Metric
            label={t('ephyraeFull')}
            value={formatMeasurementCount(
              weeklyMeasurement && showWeeklyMeasurementSummary
                ? weeklyMeasurement.ephyrae_count
                : box.latest_measurement?.ephyrae_count,
            )}
          />

          <div className="last-reading-comment">
            <div className="last-reading-comment-header">
              <small>{t('lastComment')}</small>
              {showWeeklyMeasurementSummary && weeklyMeasurement && !weeklyMeasurement.can_edit ? (
                <p className="measurement-lock-note">
                  {weeklyMeasurement.edit_restriction === 'edit_window_expired'
                    ? t('weeklyMeasurementWindowExpired')
                    : t('weeklyMeasurementReadOnly')}
                </p>
              ) : null}
            </div>
            {showWeeklyMeasurementSummary ? (
              summaryComment ? <p>{summaryComment}</p> : null
            ) : (
              <p>{lastComment || t('noComment')}</p>
            )}
          </div>
          {showWeeklyMeasurementSummary && weeklyMeasurement?.can_edit ? (
            <button
              className="icon-button measurement-summary-edit-button"
              type="button"
              aria-label={t('modifyWeeklyMeasurement')}
              title={t('modifyWeeklyMeasurement')}
              onClick={openWeeklyMeasurementEditor}
            >
              <Pencil aria-hidden="true" size={18} />
            </button>
          ) : null}
        </section>
        ) : null}

        <LatestMovementBanner
          box={box}
          zones={zones}
          language={language}
          canEdit={canWriteLabData}
          isDesktop={isDesktopApp}
          onCorrect={onCorrectMovement}
          onRefresh={onRefreshMovementState}
          isOperationCurrent={() => operationLifetimeRef.current && isOperationCurrent()}
          t={t}
        />

        {canShowMeasurementForm ? (
          <section className={`box-section measurement-form-section${isMeasurementEditorExpanded ? ' measurement-module is-expanded' : ''}${isMeasurementFormLocked ? ' is-locked' : ''}`}>
            <form className="fake-form" onSubmit={handleSubmit}>
              <fieldset
                className="measurement-editor-fields"
                disabled={isMeasurementFormLocked}
                aria-describedby={isMeasurementFormLocked ? `measurement-lock-${box.id}` : undefined}
              >
              <div className="section-title">
                <div>
                  <h2>
                    {t(
                      editingMeasurementId != null
                        ? isCorrectingFromHistory
                          ? 'correctMeasurement'
                          : 'modifyWeeklyMeasurement'
                        : 'newMeasurement',
                    )}
                  </h2>
                  {isMeasurementFormLocked && weeklyMeasurement ? (
                    <p className="measurement-lock-note" id={`measurement-lock-${box.id}`}>
                      {weeklyMeasurement.edit_restriction === 'edit_window_expired'
                        ? t('weeklyMeasurementWindowExpired')
                        : t('weeklyMeasurementReadOnly')}
                    </p>
                  ) : null}
                </div>
                <span>{formatDisplayDate(form.measuredOn)}</span>
              </div>

              <div className="measurement-entry-grid">
                <label className="measurement-date-field">
                  {t('measurementDate')}
                  <input
                    disabled={editingMeasurementId != null}
                    required
                    type="date"
                    value={form.measuredOn}
                    onChange={(event) => setForm((current) => ({ ...current, measuredOn: event.target.value }))}
                  />
                </label>

                <div className="measurement-count-field measurement-polyp-field">
                  <label className="measurement-field-label" htmlFor="measurement-polyps">{t('polyps')}</label>
                  <div className="count-stepper">
                    <StepperButton
                      aria-label={`${t('polyps')} -1`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        polypCount: decrementCountValue(current.polypCount),
                      }))}
                    >
                      <PolypbaseIcon name="minus" size={18} />
                    </StepperButton>
                    <input
                      min="0"
                      required
                      inputMode="numeric"
                      placeholder="0"
                      type="number"
                      id="measurement-polyps"
                      value={form.polypCount}
                      onChange={(event) => setForm((current) => ({ ...current, polypCount: event.target.value }))}
                    />
                    <StepperButton
                      aria-label={`${t('polyps')} +1`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        polypCount: incrementCountValue(current.polypCount, 1),
                      }))}
                    >
                      <PolypbaseIcon name="plus" size={18} />
                    </StepperButton>
                  </div>
                  <QuickCountButtons
                    values={[50, 100]}
                    onAdd={(value) => setForm((current) => ({
                      ...current,
                      polypCount: incrementCountValue(current.polypCount, value),
                    }))}
                  />
                </div>

                <div className="measurement-count-field measurement-ephyrae-field">
                  <label className="measurement-field-label" htmlFor="measurement-ephyrae">{t('ephyraeFull')}</label>
                  <div className="count-stepper">
                    <StepperButton
                      aria-label={`${t('ephyraeFull')} -1`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        ephyraeCount: decrementCountValue(current.ephyraeCount),
                      }))}
                    >
                      <PolypbaseIcon name="minus" size={18} />
                    </StepperButton>
                    <input
                      min="0"
                      required
                      inputMode="numeric"
                      placeholder="0"
                      type="number"
                      id="measurement-ephyrae"
                      value={form.ephyraeCount}
                      onChange={(event) => setForm((current) => ({ ...current, ephyraeCount: event.target.value }))}
                    />
                    <StepperButton
                      aria-label={`${t('ephyraeFull')} +1`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        ephyraeCount: incrementCountValue(current.ephyraeCount, 1),
                      }))}
                    >
                      <PolypbaseIcon name="plus" size={18} />
                    </StepperButton>
                  </div>
                  <QuickCountButtons
                    values={[10, 25]}
                    onAdd={(value) => setForm((current) => ({
                      ...current,
                      ephyraeCount: incrementCountValue(current.ephyraeCount, value),
                    }))}
                  />
                </div>

                <div className="measurement-salinity-field">
                  <label className="measurement-field-label" htmlFor="measurement-salinity">{t('salinityFull')}</label>
                  <div className="count-stepper count-stepper-salinity">
                    <StepperButton
                      aria-label={`${t('salinityFull')} -${SALINITY_STEP.toLocaleString(language)}`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        salinity: stepBiologicalSalinity(current.salinity, -SALINITY_STEP),
                      }))}
                    >
                      <PolypbaseIcon name="minus" size={18} />
                    </StepperButton>

                    <input
                      min="0"
                      inputMode="decimal"
                      step="0.01"
                      placeholder={String(SALINITY_STEP)}
                      type="number"
                      id="measurement-salinity"
                      value={form.salinity}
                      onChange={(event) => setForm((current) => ({ ...current, salinity: event.target.value }))}
                    />
                    <StepperButton
                      aria-label={`${t('salinityFull')} +${SALINITY_STEP.toLocaleString(language)}`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        salinity: stepBiologicalSalinity(current.salinity, SALINITY_STEP),
                      }))}
                    >
                      <PolypbaseIcon name="plus" size={18} />
                    </StepperButton>
                  </div>
                  <QuickCountButtons
                    values={[1, 5]}
                    getAccessibleLabel={(value) => `${t('salinityFull')} +${value.toLocaleString(language)} PSU`}
                    onAdd={(value) => setForm((current) => ({
                      ...current,
                      salinity: stepBiologicalSalinity(current.salinity, value),
                    }))}
                  />
                </div>
              </div>

              <label className="notes-field">
                <span className="measurement-field-label">{t('observation')}</span>
                <textarea
                  placeholder={t('observationPlaceholder')}
                  rows={3}
                  value={form.notes}
                  onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))}
                />
              </label>

              {saveError ? <p className="inline-error form-feedback">{saveError}</p> : null}

              <div className="measurement-actions-row">
                <MeasurementSaveButton
                  isDesktop={isDesktopApp}
                  isDisabled={isMeasurementFormLocked || isMeasurementDraftUnchanged}
                  isSaving={isSaving}
                  labels={{
                    hold: editingMeasurementId != null ? t('holdToUpdate') : t('holdToSave'),
                    save: editingMeasurementId != null
                      ? t('saveMeasurementEdit')
                      : t('saveMeasurement'),
                    saving: t('saving'),
                  }}
                  onSave={saveMeasurement}
                />
                {editingMeasurementId != null ? (
                  <button
                    className="secondary-button measurement-cancel-button"
                    type="button"
                    disabled={isSaving}
                    onClick={cancelMeasurementEdit}
                  >
                    {t('cancelEdit')}
                  </button>
                ) : null}
              </div>
              </fieldset>
            </form>
          </section>
        ) : null}

        {subcultureSuccess ? (
          <p className="subculture-success" role="status">
            <strong>{t('subcultureCompleted')}</strong>
            {typeof subcultureSuccess.allocated_polyp_count === 'number' && typeof subcultureSuccess.parent_polyp_count_after === 'number'
                && subcultureSuccess.allocations.length > 0 && subcultureSuccess.allocations.every((allocation) => allocation.allocated_polyps !== null) ? (
              <>{' '}{subcultureSuccess.parent_polyp_count_before} → {subcultureSuccess.parent_polyp_count_after} {t('polyps').toLocaleLowerCase()}</>
            ) : null}
            {' — '}{subcultureSuccess.children.map((child) => child.global_code).join(', ')}
          </p>
        ) : null}

        <section className="box-insights-section">
          <BoxInsights
            boxId={box.id}
            activeTab={activeInsightTab}
            graph={lineageGraph}
            graphError={lineageGraphError}
            isGraphLoading={isLineageGraphLoading}
            labels={getBoxInsightsLabels(t)}
            language={language}
            lineage={lineage}
            locations={'locations' in box ? box.locations : []}
            temperatures={'temperature_history' in box ? box.temperature_history : []}
            measurements={measurements}
            biologicalTimeline={'biological_timeline' in box ? box.biological_timeline : undefined}
            movements={getBoxMovements(box)}
            onLoadLineageGraph={handleLoadLineageGraph}
            onOpenHistory={() => setIsHistoryOpen(true)}
            onSelectBox={onOpenBox}
            onSelectTab={setActiveInsightTab}
          />
        </section>

        {isHistoryOpen ? (
          <MeasurementHistoryModal
            boxCode={box.global_code}
            labels={getBoxInsightsLabels(t)}
            language={language}
            measurements={measurements}
            biologicalTimeline={'biological_timeline' in box ? box.biological_timeline : undefined}
            onClose={() => setIsHistoryOpen(false)}
          />
        ) : null}

        {isMoveOpen ? (
          <MoveBoxModal
            box={box}
            zones={zones}
            language={language}
            isSaving={isSavingMove}
            error={moveError}
            onClose={() => setIsMoveOpen(false)}
            onSubmit={handleMove}
          />
        ) : null}

        {isSubcultureOpen ? (
          <SubcultureModal
            box={box}
            zones={zones}
            language={language}
            isSaving={isSavingSubculture}
            error={subcultureError}
            onClose={() => setIsSubcultureOpen(false)}
            onSubmit={handleSubculture}
          />
        ) : null}

        {lifecycleAction && (lifecycleAction === 'deactivate' || lifecycleAction === 'reactivate') ? (
          <BoxLifecycleModal
            action={lifecycleAction}
            box={box}
            error={statusError}
            isSaving={isChangingBoxStatus}
            onClose={() => {
              if (!isChangingBoxStatus) setLifecycleAction(null);
            }}
            onSubmit={handleLifecycleSubmit}
            t={t}
            zones={zones}
          />
        ) : null}

        {isQrLabelOpen && qr ? (
          <QrLabelModal
            box={box}
            labels={{
              addToSelection: t('qrLabelAddToSelection'),
              alreadySelected: t('qrLabelAlreadySelected'),
              close: t('close'),
              download: t('qrLabelDownload'),
              qrLabelPreparing: t('qrLabelPreparing'),
              qrLabelPopupBlocked: t('qrLabelPopupBlocked'),
              qrLabelQrUnavailable: t('qrLabelQrUnavailable'),
              qrLabelResourceUnavailable: t('qrLabelResourceUnavailable'),
              qrLabelImagePreparationFailed: t('qrLabelImagePreparationFailed'),
              qrLabelPreparationFailed: t('qrLabelPreparationFailed'),
              qrLabelRetry: t('qrLabelRetry'),
              print: t('print'),
              qrCode: t('qrCode'),
              selectionCount: t('qrLabelSelectionCount'),
              title: t('qrLabelTitle'),
              viewSelection: t('qrLabelViewSelection'),
            }}
            qrImageUrl={qr.imageUrl}
            selectedLabels={qrLabelSelection}
            onAddToSelection={onAddQrLabel}
            onClose={() => setIsQrLabelOpen(false)}
            onViewSelection={() => {
              setIsQrLabelOpen(false);
              onOpenQrLabelSelection();
            }}
          />
        ) : null}
      </div>
    </section>
  );
}


function getInitialMeasurementForm(
  defaultSalinity = '',
  measuredOn = getTodayDateValue(),
) {
  return {
    measuredOn,
    polypCount: '',
    ephyraeCount: '',
    salinity: defaultSalinity,
    notes: '',
  };
}

/**
 * Control salinity of the box: the one maintained on its zone.
 *
 * A new measurement starts from it, since that is the environment the box is
 * known to sit in. Normalized without rounding so the API's "30.00"
 * reaches the field as "30" and the +/- buttons keep working from there.
 */
function getZoneSalinityValue(box: BoxItem | BoxDetail | null, zones: ThermalZone[]) {
  if (!box) return '';
  const salinity = getCurrentThermalZone(box, zones)?.salinity_psu;
  if (salinity === null || salinity === undefined || salinity === '') return '';
  return formatBiologicalSalinity(salinity);
}

/**
 * Salinity a new measurement starts from.
 *
 * Priority: the box's own recorded salinity, once a measurement has set one --
 * that is the value the technician last decided for this box. Otherwise the
 * zone's control salinity, and finally empty. Normalised so the API's "31.00"
 * reaches the field as "31" and the +/- buttons keep working from there.
 */
function getDefaultMeasurementSalinity(box: BoxItem | BoxDetail | null, zones: ThermalZone[]) {
  const boxSalinity = box?.latest_salinity_psu;
  if (boxSalinity !== null && boxSalinity !== undefined && boxSalinity !== '') {
    return formatBiologicalSalinity(boxSalinity);
  }
  return getZoneSalinityValue(box, zones);
}

function getTodayDateValue() {
  const today = new Date();
  today.setMinutes(today.getMinutes() - today.getTimezoneOffset());
  return today.toISOString().slice(0, 10);
}

function getMeasurementHistory(box: BoxItem | BoxDetail) {
  if ('biological_measurements' in box) {
    return box.biological_measurements;
  }

  return box.latest_measurement ? [box.latest_measurement] : [];
}

function getLatestComment(measurements: BiologicalMeasurement[], box: BoxItem | BoxDetail) {
  const measurementWithComment = measurements.find((measurement) => measurement.notes?.trim());
  return measurementWithComment?.notes?.trim() || box.latest_measurement?.notes?.trim();
}

function getBoxCreatedDate(box: BoxItem | BoxDetail) {
  if ('created_on' in box) {
    return box.created_on;
  }
  return box.entered_on;
}

function getFirstMeasurementDate(measurements: BiologicalMeasurement[]) {
  if (!measurements.length) return null;
  return measurements
    .map((measurement) => measurement.measured_on)
    .filter(Boolean)
    .sort((first, second) => first.localeCompare(second))[0] ?? null;
}

function getBoxDisplayDate(
  box: BoxItem | BoxDetail,
  measurements: BiologicalMeasurement[],
): { labelKey: TranslationKey; date: string | null } {
  const createdOn = getBoxCreatedDate(box);
  const firstMeasurementOn = getFirstMeasurementDate(measurements);

  if (firstMeasurementOn && (!createdOn || firstMeasurementOn < createdOn)) {
    return { labelKey: 'firstMeasurementOn', date: firstMeasurementOn };
  }

  return { labelKey: 'createdOn', date: createdOn };
}

// Builds the payload sent to the API. Kept in one place so the dirty-state
// check compares exactly what a save would send.
function buildMeasurementPayload(form: {
  measuredOn: string;
  polypCount: string;
  ephyraeCount: string;
  salinity: string;
  notes: string;
}): MeasurementPayload {
  const polypCount = parsePositiveInteger(form.polypCount);
  const ephyraeCount = parsePositiveInteger(form.ephyraeCount);
  if (polypCount == null || ephyraeCount == null) throw new Error('Invalid measurement counts.');
  return {
    measured_on: form.measuredOn,
    polyp_count: polypCount,
    ephyrae_count: ephyraeCount,
    salinity_psu: form.salinity.trim() || null,
    notes: form.notes.trim(),
  };
}

function parsePositiveInteger(value: string) {
  const match = /^(\d+(?:\.\d*)?|\.\d+)(?:e([+-]?\d+))?$/i.exec(value.trim());
  if (!match) return null;
  const [whole, fraction = ''] = match[1].split('.');
  const digits = `${whole}${fraction}`.replace(/^0+/, '');
  if (!digits) return 0;
  const exponent = Number(match[2] ?? '0');
  if (!Number.isSafeInteger(exponent)) return null;
  const scale = exponent - fraction.length;
  // Check decimal digits before conversion so rounding/underflow cannot turn a fraction into an integer or zero.
  if (digits.length + scale > 10 || digits.length + scale <= 0) return null;
  let integerText: string;
  if (scale < 0) {
    if (!/^0+$/.test(digits.slice(scale))) return null;
    integerText = digits.slice(0, scale);
  } else {
    integerText = digits + '0'.repeat(scale);
  }
  const parsedValue = Number(integerText);
  return parsedValue <= 2147483647 ? parsedValue : null;
}

function incrementCountValue(currentValue: string, increment: number) {
  const parsed = parsePositiveInteger(currentValue);
  if (parsed == null) return currentValue.trim() ? currentValue : String(increment);
  return String(Math.min(parsed + increment, 2147483647));
}

function decrementCountValue(currentValue: string) {
  const parsed = parsePositiveInteger(currentValue);
  if (parsed == null) return currentValue;
  return String(Math.max(parsed - 1, 0));
}

function getMeasurementSaveError(error: unknown, t: TFunction) {
  if (isMeasurementWeekConflict(error)) {
    return t('measurementWeekConflict');
  }
  if (isMeasurementEditWindowExpired(error)) {
    return t('weeklyMeasurementWindowExpired');
  }
  if (error instanceof ApiError && error.status === 403) {
    return t('measurementForbidden');
  }

  return getErrorMessage(error);
}

function getBoxLineage(box: BoxItem | BoxDetail): BoxLineage {
  if ('lineage' in box) {
    return box.lineage;
  }

  return { parents: [], children: [] };
}

function getBoxMovements(box: BoxItem | BoxDetail): BoxMovement[] {
  return 'movements' in box ? box.movements : [];
}

function getCurrentThermalZone(box: BoxItem | BoxDetail, zones: ThermalZone[]) {
  if (!box.thermal_zone) return null;
  return zones.find((zone) => zone.id === box.thermal_zone?.id) ?? null;
}

function getSubcultureSaveError(error: unknown, t: TFunction) {
  if (error instanceof ApiError && error.status === 403) {
    return t('subcultureForbidden');
  }
  if (error instanceof ApiError && error.status === 409 && error.data
      && typeof error.data === 'object' && 'code' in error.data
      && error.data.code === 'subculture_current_state_changed') {
    return t('subcultureStateChanged');
  }
  return getErrorMessage(error);
}

function getMoveSaveError(error: unknown, t: TFunction) {
  if (isBoxLocationChangedError(error)) {
    return t('moveLocationChanged');
  }
  if (error instanceof ApiError && error.status === 403) {
    return t('moveForbidden');
  }

  return getErrorMessage(error);
}

function userCanArchiveBox(profile: UserProfile | null, organizationId: number) {
  if (!profile) return false;
  if (profile.is_superuser) return true;

  return profile.memberships.some(
    (membership) => membership.organization.id === organizationId && membership.role === 'admin',
  );
}
