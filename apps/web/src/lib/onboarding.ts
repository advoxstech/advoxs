export type OnboardingStep = {
  key: string;
  label: string;
  description: string;
  kind: "main" | "recommended" | "milestone";
  completed: boolean;
  action_label: string;
  action_href: string;
};

export type OnboardingProgress = {
  completed: boolean;
  main_configuration_complete: boolean;
  completed_steps: number;
  total_steps: number;
  steps: OnboardingStep[];
};
