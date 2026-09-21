<template>
  <div class="empty">
    <p>{{ text }}</p>
    <slot />
  </div>
</template>

<script setup lang="ts">
defineProps<{ text?: string }>()
</script>

<style scoped>
.empty {
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  padding: 28px 16px;
  text-align: center;
  color: var(--muted);
  background: #fbfcfe;
}
.empty p {
  margin: 0 0 8px;
}
</style>
