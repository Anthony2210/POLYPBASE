export const sharing = {
  shareAbsent: { fr: 'Absent de la source', en: 'Absent from source' },
  shareAccept: { fr: 'Accepter l’invitation', en: 'Accept invitation' },
  shareAcceptedAt: { fr: 'Accepté le', en: 'Accepted' },
  shareAccessExpiry: { fr: 'Expiration de l’accès', en: 'Access expires' },
  shareAddedFields: { fr: 'Informations ajoutées', en: 'Added information' },
  shareAreas: { fr: 'Partages scientifiques', en: 'Scientific sharing' },
  shareBlank: { fr: 'Vide', en: 'Blank' },
  shareBoxCode: { fr: 'Code de la boîte', en: 'Box code' },
  shareBoxPages: {
    fr: 'Pages des résultats de recherche',
    en: 'Search result pages',
  },
  shareBoxSearchHint: { fr: 'Code de boîte', en: 'Box code' },
  shareCaptureHash: {
    fr: 'Empreinte SHA-256 de la capture',
    en: 'Capture SHA-256 digest',
  },
  shareCapturedAt: { fr: 'Capture enregistrée le', en: 'Capture recorded' },
  shareChangeBox: { fr: 'Changer de boîte', en: 'Change box' },
  shareCheckingAccess: {
    fr: 'Vérification de votre accès…',
    en: 'Checking your access…',
  },
  shareConfirmDisclosure: {
    fr: 'Vérifiez le destinataire, les données et les dates avant de confirmer cette divulgation scientifique.',
    en: 'Check the recipient, data and dates before confirming this scientific disclosure.',
  },
  shareConfirmExpansion: {
    fr: 'La sélection divulgue des informations supplémentaires. Confirmer autorise explicitement cette extension.',
    en: 'The selection discloses additional information. Confirming explicitly authorizes this expansion.',
  },
  shareConfirmRecapture: {
    fr: 'Une nouvelle capture de la source actuelle sera créée pour cette révision. Les captures précédentes ne seront pas réécrites.',
    en: 'A new capture of the current source will be created for this revision. Earlier captures will not be rewritten.',
  },
  shareCreate: { fr: 'Partager une boîte', en: 'Share a box' },
  shareCreateConfirm: {
    fr: 'Créer le partage et envoyer l’invitation',
    en: 'Create share and send invitation',
  },
  shareCreatedAt: { fr: 'Créé le', en: 'Created' },
  shareDeliveryFailed: { fr: 'Échec de l’envoi', en: 'Delivery failed' },
  shareDeliveryHelp: {
    fr: 'Aucune nouvelle tentative automatique. Si nécessaire, régénérez manuellement l’invitation.',
    en: 'There is no automatic retry. Regenerate the invitation manually if needed.',
  },
  shareDeliveryPending: {
    fr: 'Envoi en attente de confirmation',
    en: 'Delivery awaiting confirmation',
  },
  shareDeliverySent: {
    fr: 'Invitation remise au relais SMTP',
    en: 'Invitation accepted by SMTP relay',
  },
  shareDeliveryUncertain: { fr: 'Envoi incertain', en: 'Delivery uncertain' },
  shareDeliveryUpdated: {
    fr: 'État de l’envoi mis à jour le',
    en: 'Delivery status updated',
  },
  shareDirectEntry: { fr: 'Saisie directe', en: 'Direct entry' },
  shareDisclosure: { fr: 'Données partagées', en: 'Shared data' },
  shareDisclosureHelp: {
    fr: 'Sélectionnez chaque information à partager. Les champs non sélectionnés ne sont pas divulgués. Le nom de la structure propriétaire est obligatoire.',
    en: 'Select each piece of information to share. Unselected fields are not disclosed. The owning institution name is required.',
  },
  shareEdit: { fr: 'Modifier le partage', en: 'Edit share' },
  shareEphyrae: { fr: 'Éphyrules', en: 'Ephyrae' },
  shareErrorBox: {
    fr: 'Cette boîte ne peut pas être partagée : elle est indisponible, fusionnée ou hors de la structure active. Vérifiez la boîte auprès d’un administrateur.',
    en: 'This box cannot be shared: it is unavailable, merged or outside the active institution. Check the box with an administrator.',
  },
  shareErrorChanged: {
    fr: 'Les données ou la politique ont changé pendant la lecture. Actualisez l’aperçu ou la page avant de poursuivre.',
    en: 'The data or policy changed during the read. Refresh the preview or page before continuing.',
  },
  shareErrorConfiguration: {
    fr: 'Le partage n’est pas encore configuré : les limites de validité doivent être définies côté serveur. La création, la prolongation et la régénération restent indisponibles. Les partages existants restent consultables selon vos droits.',
    en: 'Sharing is not configured yet: lifetime limits must be set on the server. Creation, extension and regeneration remain unavailable. Existing shares can still be inspected according to your permissions.',
  },
  shareErrorDates: {
    fr: 'Saisissez des dates futures en UTC, dans les limites indiquées. L’invitation doit expirer au plus tard à la fin de l’accès. Un accès avec invitation en attente ne peut pas finir avant celle-ci.',
    en: 'Enter future UTC dates within the indicated limits. The invitation must expire no later than access. Access with a pending invitation cannot end before that invitation.',
  },
  shareErrorDelivery: {
    fr: 'L’envoi des invitations est désactivé côté serveur. Contactez un administrateur avant de créer ou régénérer une invitation.',
    en: 'Invitation delivery is disabled on the server. Contact an administrator before creating or regenerating an invitation.',
  },
  shareErrorDisclosure: {
    fr: 'Cette sélection n’est pas valide. Vérifiez les champs obligatoires et leurs dépendances, puis recalculez l’aperçu.',
    en: 'This selection is invalid. Check required fields and their dependencies, then recalculate the preview.',
  },
  shareErrorEmail: {
    fr: 'Saisissez une adresse e-mail valide pour le destinataire.',
    en: 'Enter a valid recipient email address.',
  },
  shareErrorExists: {
    fr: 'Un partage non révoqué existe déjà pour cette boîte et cette adresse. Retrouvez-le dans les envois pour le modifier.',
    en: 'A non-revoked share already exists for this box and email address. Find it in outgoing shares to edit it.',
  },
  shareErrorExpired: {
    fr: 'Cet accès a expiré. Contactez la structure source pour demander sa prolongation.',
    en: 'This access has expired. Contact the source institution to request an extension.',
  },
  shareErrorFrozen: {
    fr: 'La capture figée n’est pas disponible dans cet environnement. Choisissez les données actualisées, ou contactez un administrateur pour faire configurer la capture.',
    en: 'Frozen capture is unavailable in this environment. Choose live data, or contact an administrator to configure captures.',
  },
  shareErrorInvitation: {
    fr: 'Cette invitation est indisponible : le lien peut être invalide, remplacé, déjà accepté ou révoqué. Consultez vos partages reçus ou demandez un nouveau lien à la structure source.',
    en: 'This invitation is unavailable: the link may be invalid, replaced, already accepted or revoked. Check your received shares or request a new link from the source institution.',
  },
  shareErrorInvitationExpired: {
    fr: 'L’invitation a expiré. Demandez un nouveau lien à la structure source.',
    en: 'The invitation has expired. Request a new link from the source institution.',
  },
  shareErrorMismatch: {
    fr: 'Cette invitation ne correspond pas au compte connecté. Déconnectez-vous, puis rouvrez le lien original avec le compte destinataire. Merci de contacter un administrateur en cas de difficulté.',
    en: 'This invitation does not match the signed-in account. Sign out, then reopen the original link with the recipient account. Please contact your administrator if you need help.',
  },
  shareErrorNetwork: {
    fr: 'La réponse du serveur n’a pas pu être confirmée. Vérifiez la connexion et actualisez le suivi avant de renouveler une action. Une écriture peut déjà avoir été enregistrée.',
    en: 'The server response could not be confirmed. Check the connection and refresh tracking before repeating an action. A write may already have been recorded.',
  },
  shareErrorNotFound: {
    fr: 'Ce partage est introuvable ou ne vous est pas accessible. Vérifiez le lien ou contactez la structure qui vous l’a transmis.',
    en: 'This share cannot be found or is not accessible to you. Check the link or contact the institution that sent it.',
  },
  shareErrorNotPending: {
    fr: 'Cette invitation n’est plus en attente. Actualisez le suivi avant de poursuivre.',
    en: 'This invitation is no longer pending. Refresh tracking before continuing.',
  },
  shareErrorPermission: {
    fr: 'Cet accès n’est pas autorisé. Merci de contacter un administrateur.',
    en: 'This access is not authorized. Please contact your administrator.',
  },
  shareErrorRevision: {
    fr: 'La politique a été modifiée par une autre opération. Relisez la version actuelle avant de modifier le partage. Votre brouillon ne sera pas enregistré automatiquement.',
    en: 'Another operation changed the policy. Reload the current version before editing the share. Your draft will not be saved automatically.',
  },
  shareErrorRevoked: {
    fr: 'Cet accès a été révoqué. Aucune donnée scientifique n’est affichée. Contactez la structure source pour toute nouvelle demande.',
    en: 'This access has been revoked. No scientific data is displayed. Contact the source institution for any new request.',
  },
  shareErrorSource: {
    fr: 'Les données partagées sont indisponibles auprès de la source. Réessayez ultérieurement ou contactez la structure source.',
    en: 'Shared data is unavailable at the source. Try again later or contact the source institution.',
  },
  shareErrorValidation: {
    fr: 'Vérifiez la boîte, le destinataire, les champs obligatoires, le mode et les dates, puis calculez l’aperçu avant de confirmer.',
    en: 'Check the box, recipient, required fields, mode and dates, then calculate the preview before confirming.',
  },
  shareFileImport: { fr: 'Import de fichier', en: 'File import' },
  shareFilterBox: { fr: 'Filtrer par boîte', en: 'Filter by box' },
  shareFindBox: {
    fr: 'Rechercher une boîte de la structure active',
    en: 'Find a box in the active institution',
  },
  shareFrozen: { fr: 'Instantané figé', en: 'Frozen snapshot' },
  shareFrozenHelp: {
    fr: 'Le destinataire consulte une capture enregistrée, sans les modifications ultérieures de la source. Une nouvelle capture nécessite une confirmation explicite.',
    en: 'The recipient sees a recorded capture, without later source changes. A new capture requires explicit confirmation.',
  },
  shareHistoryEmpty: {
    fr: 'L’historique est autorisé, mais ne contient aucun relevé.',
    en: 'History is authorized but contains no records.',
  },
  shareHistoryPages: {
    fr: 'Pages des relevés autorisés',
    en: 'Authorized record pages',
  },
  shareHistoryScope: {
    fr: 'La sélection de l’historique partage tous les relevés disponibles pour les champs choisis. Aucune restriction de période n’est appliquée.',
    en: 'Selecting history shares all available records for the chosen fields. No time-range restriction is applied.',
  },
  shareHistoryScroll: {
    fr: 'Si le tableau dépasse la largeur disponible, faites-le défiler horizontalement pour consulter tous les champs autorisés.',
    en: 'If the table exceeds the available width, scroll it horizontally to view all authorized fields.',
  },
  shareHistoryUndisclosed: {
    fr: 'L’historique des relevés n’est pas divulgué dans ce partage.',
    en: 'Record history is not disclosed in this share.',
  },
  shareIdentity: { fr: 'Identité scientifique', en: 'Scientific identity' },
  shareInstitution: { fr: 'Structure propriétaire', en: 'Owning institution' },
  shareInstitutionId: {
    fr: 'Identifiant portable de la structure',
    en: 'Institution portable identifier',
  },
  shareInvitationExpiry: {
    fr: 'Expiration de l’invitation',
    en: 'Invitation expires',
  },
  shareInvitationFrom: {
    fr: '{institution} vous invite à consulter les données scientifiques d’une boîte.',
    en: '{institution} invites you to view the scientific data of a box.',
  },
  shareInvitationReadOnly: {
    fr: 'Cet accès personnel est strictement en lecture seule. Il ne permet ni de modifier la boîte ni de parcourir l’inventaire de la structure source.',
    en: 'This personal access is strictly read-only. It does not allow editing the box or browsing the source institution’s inventory.',
  },
  shareInvitationSignIn: {
    fr: 'Connectez-vous avec le compte correspondant à l’adresse invitée. La connexion n’accepte pas automatiquement l’invitation.',
    en: 'Sign in with the account matching the invited email address. Signing in does not automatically accept the invitation.',
  },
  shareInvitationTitle: {
    fr: 'Invitation à consulter une boîte',
    en: 'Invitation to view a box',
  },
  shareKeepExpiry: {
    fr: 'Pour conserver l’expiration actuelle, laissez ce champ vide : {date}.',
    en: 'Leave this field empty to keep the current expiry: {date}.',
  },
  shareLegacyDate: {
    fr: 'Précision historique non vérifiée — aucun jour exact attesté',
    en: 'Unverified historical precision — no attested exact day',
  },
  shareLegacyOrigin: {
    fr: 'Origine historique non classée',
    en: 'Unclassified historical origin',
  },
  shareLifetimeLimit: {
    fr: 'Durée maximale à partir de la demande : {hours} h.',
    en: 'Maximum lifetime from the request: {hours} h.',
  },
  shareLive: { fr: 'Données actualisées', en: 'Live data' },
  shareLiveHelp: {
    fr: 'À chaque consultation, les informations autorisées reflètent les mises à jour et corrections de la source.',
    en: 'On each visit, authorized information reflects updates and corrections to the source.',
  },
  shareMode: { fr: 'Mode de partage', en: 'Sharing mode' },
  shareNext: { fr: 'Suivant', en: 'Next' },
  shareNoAccount: {
    fr: 'L’acceptation nécessite un compte POLYPBASE existant. Si vous n’en avez pas, contactez la structure qui vous a invité. Aucune appartenance à cette structure n’est créée par le partage.',
    en: 'Acceptance requires an existing POLYPBASE account. If you do not have one, contact the inviting institution. Sharing does not create membership in that institution.',
  },
  shareNoBox: {
    fr: 'Aucune boîte accessible ne correspond à cette recherche. Vérifiez le code ou modifiez la recherche.',
    en: 'No accessible box matches this search. Check the code or change the search.',
  },
  shareNoMatchingOutgoing: {
    fr: 'Aucun partage pour cette boîte. Retirez le filtre pour consulter les autres envois.',
    en: 'No shares for this box. Clear the filter to view other outgoing shares.',
  },
  shareNoOutgoing: {
    fr: 'Aucun partage enregistré. Utilisez « Partager une boîte » pour préparer une invitation si le partage est configuré.',
    en: 'No shares recorded. Use “Share a box” to prepare an invitation when sharing is configured.',
  },
  shareNoReceived: {
    fr: 'Aucun partage accepté pour ce compte. Pour recevoir un accès, ouvrez le lien personnel envoyé par la structure propriétaire et acceptez l’invitation.',
    en: 'No accepted shares for this account. To receive access, open the personal link sent by the owning institution and accept the invitation.',
  },
  shareNonAtomic: {
    fr: 'Cette projection rassemble plusieurs lectures de la source. Elle ne constitue pas un instantané scientifique atomique.',
    en: 'This projection combines several source reads. It is not an atomic scientific snapshot.',
  },
  shareNull: { fr: 'Inconnu (NULL)', en: 'Unknown (NULL)' },
  shareObservationTime: { fr: 'Temps du relevé', en: 'Record time' },
  shareObservations: { fr: 'Historique des relevés', en: 'Record history' },
  shareObserver: { fr: 'Statut de l’observateur', en: 'Observer status' },
  shareObserverKnown: { fr: 'Observateur connu', en: 'Known observer' },
  shareObserverLegacy: {
    fr: 'Observateur historique non établi',
    en: 'Historical observer not established',
  },
  shareObserverNotRecorded: {
    fr: 'Observateur non enregistré',
    en: 'Observer not recorded',
  },
  shareOrigin: { fr: 'Origine du relevé', en: 'Record origin' },
  shareOutgoing: { fr: 'Envois', en: 'Sending' },
  shareOutgoingPages: { fr: 'Pages des envois', en: 'Outgoing share pages' },
  sharePage: { fr: 'Page {page}', en: 'Page {page}' },
  sharePolyps: { fr: 'Polypes', en: 'Polyps' },
  sharePreview: {
    fr: 'Calculer l’aperçu des données',
    en: 'Calculate data preview',
  },
  sharePreviewHelp: {
    fr: 'Contenu actuellement autorisé par cette sélection. Cet aperçu n’est pas une capture figée : la source peut évoluer avant la création du partage.',
    en: 'Content currently authorized by this selection. This preview is not a frozen capture: the source may change before the share is created.',
  },
  sharePreviewTitle: { fr: 'Aperçu des données', en: 'Data preview' },
  sharePrevious: { fr: 'Précédent', en: 'Previous' },
  shareProjectedAt: { fr: 'Projection commencée le', en: 'Projection started' },
  shareProvenance: { fr: 'Provenance des relevés', en: 'Record provenance' },
  shareRead: { fr: 'Consulter le partage', en: 'View share' },
  shareReadOnly: { fr: 'Lecture seule', en: 'Read-only' },
  shareReaderScope: {
    fr: 'Vous consultez uniquement les données scientifiques autorisées par cette structure.',
    en: 'You can view only the scientific data authorized by this institution.',
  },
  shareReaderSignIn: {
    fr: 'Connectez-vous pour consulter vos partages personnels.',
    en: 'Sign in to view your personal shares.',
  },
  shareReceived: { fr: 'Boîtes partagées reçues', en: 'Received box shares' },
  shareReceivedHelp: {
    fr: 'Vos accès personnels acceptés, indépendants de vos appartenances institutionnelles. Chaque consultation est autorisée par le serveur.',
    en: 'Your accepted personal grants, independent of institutional memberships. Each visit is authorized by the server.',
  },
  shareReceivedPages: {
    fr: 'Pages des partages reçus',
    en: 'Received share pages',
  },
  shareRecipient: { fr: 'Destinataire', en: 'Recipient' },
  shareReissue: { fr: 'Régénérer l’invitation', en: 'Regenerate invitation' },
  shareReissueHelp: {
    fr: 'Le nouveau lien invalide tous les liens précédents. Saisissez une nouvelle expiration d’invitation. L’expiration de l’accès reste inchangée.',
    en: 'The new link invalidates all previous links. Enter a new invitation expiry. The access expiry stays unchanged.',
  },
  shareReloadPolicy: {
    fr: 'Relire la politique actuelle et abandonner le brouillon',
    en: 'Reload current policy and discard draft',
  },
  shareRequired: {
    fr: 'Obligatoire — à sélectionner explicitement',
    en: 'Required — select explicitly',
  },
  shareRequires: { fr: 'Nécessite : {fields}.', en: 'Requires: {fields}.' },
  shareRevision: { fr: 'Révision de la politique', en: 'Policy revision' },
  shareRevoke: { fr: 'Révoquer le partage', en: 'Revoke share' },
  shareRevokeHelp: {
    fr: 'La révocation bloque les consultations futures et invalide l’invitation. Elle ne peut pas effacer les informations déjà consultées ou téléchargées.',
    en: 'Revocation blocks future visits and invalidates the invitation. It cannot erase information already viewed or downloaded.',
  },
  shareRevokedAt: { fr: 'Révoqué le', en: 'Revoked' },
  shareRevokedSuccess: {
    fr: 'Accès révoqué. Les consultations futures sont bloquées.',
    en: 'Access revoked. Future visits are blocked.',
  },
  shareSave: { fr: 'Enregistrer les modifications', en: 'Save changes' },
  shareSaved: {
    fr: 'Partage enregistré. Vérifiez ci-dessous l’état de l’invitation et de son envoi.',
    en: 'Share saved. Check the invitation and delivery status below.',
  },
  shareScientificLegend: {
    fr: '0 est une valeur mesurée. NULL : valeur explicitement inconnue ; vide : cellule source vide ; absent : champ non fourni par la source. Une information non divulguée n’est pas une donnée manquante.',
    en: '0 is a measured value. NULL: explicitly unknown value; blank: empty source cell; absent: field not supplied by the source. Undisclosed information is not missing data.',
  },
  shareSearch: { fr: 'Rechercher', en: 'Search' },
  shareSelectedBox: { fr: 'Boîte source', en: 'Source box' },
  shareSentHelp: {
    fr: 'Le relais SMTP a accepté le message. Cela ne prouve ni sa réception ni son ouverture.',
    en: 'The SMTP relay accepted the message. This does not prove receipt or opening.',
  },
  shareSharedBox: { fr: 'Boîte partagée', en: 'Shared box' },
  shareSharedBy: {
    fr: 'Boîte partagée par {institution}',
    en: 'Box shared by {institution}',
  },
  shareSkip: { fr: 'Aller aux données partagées', en: 'Skip to shared data' },
  shareSource: { fr: 'Source', en: 'Source' },
  shareSpecies: {
    fr: 'Nom scientifique de l’espèce',
    en: 'Species scientific name',
  },
  shareStateActive: { fr: 'Accès actif', en: 'Access active' },
  shareStateExpired: { fr: 'Accès expiré', en: 'Access expired' },
  shareStateInvitationExpired: {
    fr: 'Invitation expirée',
    en: 'Invitation expired',
  },
  shareStatePending: {
    fr: 'Invitation en attente d’acceptation',
    en: 'Invitation awaiting acceptance',
  },
  shareStateRevoked: { fr: 'Accès révoqué', en: 'Access revoked' },
  shareStateUnavailable: { fr: 'Accès indisponible', en: 'Access unavailable' },
  shareStrain: { fr: 'Code de la souche', en: 'Strain code' },
  shareStrainId: {
    fr: 'Identité globale de la souche',
    en: 'Global strain identity',
  },
  shareStrobilae: { fr: 'Strobiles', en: 'Strobilae' },
  shareTracking: {
    fr: 'Afficher le suivi et les données partagées',
    en: 'View tracking and shared data',
  },
  shareTrackingHelp: {
    fr: 'Le suivi conserve les invitations, acceptations, expirations et révocations. Actualisez la liste pour relire leur état auprès du serveur.',
    en: 'Tracking retains invitations, acceptance, expiry and revocation. Refresh the list to read their current server state.',
  },
  shareUTC: {
    fr: 'Date et heure en UTC, sans conversion depuis l’heure locale.',
    en: 'Date and time in UTC, without conversion from local time.',
  },
  shareUnavailableTitle: {
    fr: 'Partage indisponible',
    en: 'Share unavailable',
  },
  shareUncertainHelp: {
    fr: 'L’envoi ne peut pas être confirmé. Le message peut avoir été envoyé. Une régénération manuelle invalide l’ancien lien.',
    en: 'Delivery cannot be confirmed. The message may have been sent. Manual regeneration invalidates the old link.',
  },
  shareUndisclosed: { fr: 'Non divulgué', en: 'Undisclosed' },
  shareUnsupported: {
    fr: 'Ce champ ou cette version scientifique n’est pas pris en charge. Demandez une mise à jour de l’application avant de poursuivre.',
    en: 'This scientific field or version is not supported. Request an application update before continuing.',
  },
  shareWeekOnly: {
    fr: 'Semaine ISO uniquement — jour non établi',
    en: 'ISO week only — day not established',
  },
  shareWorkspace: { fr: 'Envois et réceptions', en: 'Sending and receiving' },
  shareWorkspaceHelp: {
    fr: 'Partager les données scientifiques d’une boîte en lecture seule, avec un destinataire identifié.',
    en: 'Share the scientific data of one box, read-only, with an identified recipient.',
  },
};
