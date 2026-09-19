import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select';

interface Props {
  id: string;
  values: unknown[];
  labels?: Record<string, string>;
  value: unknown;
  disabled?: boolean;
  onChange: (next: unknown) => void;
}

export const optionLabel = (
  value: unknown,
  labels?: Record<string, string>
): string => labels?.[String(value)] ?? String(value);

/**
 * Radix `Select` over a JSON Schema `enum`.
 *
 * Radix addresses items by string, so the value goes out as `String(v)` and
 * comes back mapped through the declared list. Handing the trigger's string
 * to the form directly is how an `int` Literal reaches the server as `"1"`
 * and is refused by the strict decoder.
 */
const EnumSelect = ({
  id,
  values,
  labels,
  value,
  disabled,
  onChange
}: Props) => (
  <Select
    // Radix reads `''` as "nothing selected"; a null enum must give it
    // `undefined`, not an empty string it would later try to match.
    value={value === null || value === undefined ? undefined : String(value)}
    disabled={disabled}
    onValueChange={(next) => {
      const found = values.find((candidate) => String(candidate) === next);
      onChange(found === undefined ? next : found);
    }}
  >
    <SelectTrigger id={id}>
      <SelectValue />
    </SelectTrigger>
    <SelectContent>
      {values.map((candidate) => (
        <SelectItem key={String(candidate)} value={String(candidate)}>
          {optionLabel(candidate, labels)}
        </SelectItem>
      ))}
    </SelectContent>
  </Select>
);

export default EnumSelect;
