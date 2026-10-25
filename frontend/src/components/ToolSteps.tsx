import Icon from "./Icon";
export default function ToolSteps({
  items,
  active = 0,
}: {
  items: string[];
  active?: number;
}) {
  return (
    <ol className="tool-stepbar" aria-label="操作步骤">
      {items.map((item, i) => (
        <li
          className={i === active ? "current" : ""}
          key={item}
          aria-current={i === active ? "step" : undefined}
        >
          <b>{i + 1}</b>
          {item}
          {i < items.length - 1 && <Icon name="chevron" size={12} />}
        </li>
      ))}
    </ol>
  );
}
