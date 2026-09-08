type ApplicationErrorNoticeLabels = {
  action: string;
  title: string;
};

export default function ApplicationErrorNotice({
  actionHref,
  labels,
  message,
}: {
  actionHref: string;
  labels: ApplicationErrorNoticeLabels;
  message: string;
}) {
  return (
    <section className="login-notice" role="alert">
      <h2>{labels.title}</h2>
      <p>{message}</p>
      <a href={actionHref}>{labels.action}</a>
    </section>
  );
}
