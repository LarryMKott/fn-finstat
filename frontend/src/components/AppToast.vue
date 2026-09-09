<script setup>
import { toastState } from "../toast";
</script>

<template>
  <div class="toast" :class="{ show: toastState.show, error: toastState.error }">{{ toastState.msg }}</div>
</template>
