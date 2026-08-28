import { cn } from '@/lib/utils';
import React, {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState
} from 'react';

import AutoResizeTextarea from '@/components/AutoResizeTextarea';

interface Props {
  id?: string;
  className?: string;
  autoFocus?: boolean;
  placeholder?: string;
  onChange: (value: string) => void;
  onPaste?: (event: any) => void;
  onEnter?: () => void;
}

export interface InputMethods {
  reset: () => void;
  setValueExtern: (value: string) => void;
}

const Input = forwardRef<InputMethods, Props>(
  (
    { placeholder, id, className, autoFocus, onChange, onEnter, onPaste },
    ref
  ) => {
    const [isComposing, setIsComposing] = useState(false);
    const [value, setValue] = useState('');
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const reset = () => {
      setValue('');
      onChange('');
    };

    useImperativeHandle(ref, () => ({
      reset,
      setValueExtern: (value: string) => {
        setValue(value);
        onChange(value);
      }
    }));

    useEffect(() => {
      if (textareaRef.current && autoFocus) {
        textareaRef.current.focus();
      }
    }, [autoFocus]);

    const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const newValue = e.target.value;
      setValue(newValue);
      onChange(newValue);
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey && onEnter && !isComposing) {
        e.preventDefault();
        onEnter();
      }
    };

    return (
      <div className="relative w-full">
        <AutoResizeTextarea
          ref={textareaRef}
          id={id}
          autoFocus={autoFocus}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onPaste={onPaste}
          onCompositionStart={() => setIsComposing(true)}
          onCompositionEnd={() => setIsComposing(false)}
          placeholder={placeholder}
          className={cn(
            'w-full resize-none bg-transparent placeholder:text-muted-foreground focus:outline-none',
            className
          )}
          maxHeight={250}
        />
      </div>
    );
  }
);

export default Input;
