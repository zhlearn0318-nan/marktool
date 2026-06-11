/** 内页统一横幅：深墨蓝底 + 金色眉题 + 衬线大标题。 */
export default function PageBanner({
  eyebrow,
  title,
  sub,
}: {
  eyebrow: string;
  title: string;
  sub?: string;
}) {
  return (
    <section className="page-banner">
      <div className="page-banner-inner">
        <div className="eyebrow on-dark reveal">{eyebrow}</div>
        <h1 className="reveal d1">{title}</h1>
        {sub ? <p className="reveal d2">{sub}</p> : null}
      </div>
    </section>
  );
}
