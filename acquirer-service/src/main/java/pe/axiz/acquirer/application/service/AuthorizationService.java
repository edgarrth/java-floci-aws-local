package pe.axiz.acquirer.application.service;

import java.util.*;
import pe.axiz.acquirer.application.ports.out.AuthorizationStore;
import pe.axiz.acquirer.domain.Authorization;

public final class AuthorizationService {
  private final AuthorizationStore store;

  public AuthorizationService(AuthorizationStore store) {
    this.store = store;
  }

  public AuthorizationStore.Decision authorize(Authorization request, boolean declined) {
    return store.reserve(request, declined);
  }

  public AuthorizationStore.Decision get(UUID id) {
    return store
        .find(id)
        .orElseThrow(() -> new NoSuchElementException("No se encontró la autorización"));
  }
}
