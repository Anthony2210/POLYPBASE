type LoginNoticeLabels = {
  action: string;
  title: string;
};

export default function LoginNotice({
  labels,
  message,
}: {
  labels: LoginNoticeLabels;
  message: string;
}) {
  return (
    <section className="login-notice">
      <h2>{labels.title}</h2>
      <p>{message}</p>
      <a href="/login">{labels.action}</a>
    </section>
  );
}
