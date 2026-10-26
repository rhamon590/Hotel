(function(){'use strict';
  document.querySelectorAll('[data-hg-upload]').forEach(function(area){
    var input=area.querySelector('[data-hg-files]'),status=area.querySelector('[data-hg-status]'),preview=area.querySelector('[data-hg-preview]'),form=area.closest('form'),urls=[];
    input.addEventListener('change',function(){
      urls.forEach(function(u){URL.revokeObjectURL(u);});urls=[];preview.replaceChildren();
      var files=Array.from(input.files||[]),error='';
      if(files.length+Number(area.dataset.existing)>10)error='O quarto pode ter até 10 fotos, contando as já cadastradas.';
      if(files.some(function(f){return f.size>5*1024*1024;}))error='Cada foto deve ter até 5 MB.';
      if(files.some(function(f){return !['image/jpeg','image/png','image/webp'].includes(f.type);}))error='Selecione somente JPG, PNG ou WebP.';
      input.setCustomValidity(error);status.textContent=error||(files.length?files.length+' foto(s) pronta(s) para envio.':'Nenhuma nova foto selecionada.');status.classList.toggle('hg-error',!!error);
      if(error)return;
      files.forEach(function(f){var figure=document.createElement('figure'),img=document.createElement('img'),caption=document.createElement('figcaption'),url=URL.createObjectURL(f);urls.push(url);img.src=url;img.alt='Prévia do arquivo '+f.name;caption.textContent=f.name;figure.append(img,caption);preview.append(figure);});
    });
    form.addEventListener('submit',function(){if(input.files.length){status.textContent='Enviando e verificando as fotos… Aguarde a conclusão.';form.dataset.hgBusy='1';form.setAttribute('aria-busy','true');form.querySelector('button.pe-primary').textContent='Enviando fotos…';}});
  });
  var review=document.querySelector('[data-hg-review]');
  if(review){
    var picks=Array.from(review.querySelectorAll('[data-hg-pick]')),counter=review.querySelector('[data-hg-count]'),origin=review.querySelector('#hg-origin'),rights=review.querySelector('#hg-rights'),feedback=review.querySelector('[data-hg-review-status]');
    function selected(){return picks.filter(function(p){return p.checked;});}
    function update(){var count=selected().length;counter.textContent=count+' foto(s) selecionada(s)';review.querySelector('[data-hg-approve]').textContent='Aprovar '+count+' foto(s) selecionada(s)';picks.forEach(function(p){p.closest('[data-hg-card]').querySelector('[data-hg-alt-input]').required=p.checked;});}
    review.querySelectorAll('[data-hg-select]').forEach(function(b){b.addEventListener('click',function(){picks.forEach(function(p){p.checked=b.dataset.hgSelect==='all'||b.dataset.hgSelect==='pending'&&p.dataset.pending==='1';});update();});});
    review.querySelectorAll('[data-hg-room]').forEach(function(b){b.addEventListener('click',function(){picks.forEach(function(p){if(p.dataset.room===b.dataset.hgRoom)p.checked=true;});update();});});
    picks.forEach(function(p){p.addEventListener('change',update);});
    // O servidor também valida tudo; a interface apenas orienta antes do envio.
    review.addEventListener('submit',function(e){
      var chosen=selected(),action=e.submitter?e.submitter.value:'aprovar';
      if(!chosen.length||chosen.length>100){e.preventDefault();feedback.textContent='Selecione entre 1 e 100 fotos.';feedback.classList.add('hg-error');return;}
      if(action==='aprovar'&&(origin.value.trim().length<5||!rights.checked)){e.preventDefault();feedback.textContent='Informe a origem comum e confirme os direitos das fotos selecionadas.';feedback.classList.add('hg-error');origin.focus();return;}
      if(action==='ocultar'&&!window.confirm('Ocultar '+chosen.length+' foto(s) do portal público?')){e.preventDefault();return;}
      feedback.classList.remove('hg-error');feedback.textContent='Salvando '+chosen.length+' foto(s)… Aguarde.';
      review.dataset.hgBusy='1';review.setAttribute('aria-busy','true');if(e.submitter)e.submitter.textContent=action==='aprovar'?'Aprovando fotos…':'Ocultando fotos…';
    });
    update();
  }
  document.querySelectorAll('[data-hg-remove]').forEach(function(form){form.addEventListener('submit',function(e){if(!window.confirm('Remover esta foto da galeria? A imagem deixará de aparecer no portal.'))e.preventDefault();});});
  document.querySelectorAll('[data-hg-gallery]').forEach(function(gallery){
    var buttons=Array.from(gallery.querySelectorAll('[data-hg-src]')),main=gallery.querySelector('[data-hg-main]');
    buttons.forEach(function(button,index){button.addEventListener('click',function(){main.src=button.dataset.hgSrc;main.alt=button.dataset.hgAlt;gallery.querySelector('[data-hg-caption]').textContent=button.dataset.hgAlt;gallery.querySelector('[data-hg-counter]').textContent='Foto '+(index+1)+' de '+buttons.length;buttons.forEach(function(b){b.setAttribute('aria-pressed',String(b===button));});});});
  });
}());
